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

Since the screens redesign (user, 2026-09-25) those visible sentences are short markers
beside the figure ("excl. 1", "filled 1", "ref 15 Sep") with the sentence on hover, an n/a
carries its reason on hover, FX Net / Gross USD delta left the header, and a "Data" chip
counts the marks missing on the as-of date.
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
        (as_of, instrument_id, settle_date, mark_type, value, source, f"{as_of}T15:00:00-04:00"),
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
    assert header._reason_tag("no FUTURE_PX mark for CLZ6 Comdty expiry 2026-11-19 on 2026-09-17") == "no FUTURE_PX"


def test_reason_tag_spot_conversion_and_settled_and_fallback():
    assert header._reason_tag("no SPOT for USD conversion of BRL on 2026-09-17") == "no SPOT (USD conversion)"
    assert header._reason_tag(
        "settled trade t1: no official mark on or before its settlement 2026-08-01, so it cannot be frozen"
    ) == "no historical mark at settlement"
    assert header._reason_tag("") == "unpriced"
    assert header._reason_tag("something unrecognised entirely") == "unpriced"


def test_reason_tag_names_a_retired_product_as_the_blotter_does():
    from ui.tabs.blotter_pricing import RETIRED_PRODUCT_MARKER, RETIRED_PRODUCT_TAG
    from ui.tabs.blotter_pricing import _reason_tag as blotter_tag
    reason = f"trade S1: an interest rate swap, a product that {RETIRED_PRODUCT_MARKER}; no mark is read for it"
    assert header._reason_tag(reason) == RETIRED_PRODUCT_TAG == blotter_tag(reason)
    frame = _vb_frame([("S1", "IRS", reason, float("nan"))])
    assert header._unpriced_breakdown(frame) == f"1 irs: {RETIRED_PRODUCT_TAG}"


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
    assert entry["excluded_summary"] == "excludes 2 of 4 trades: 1 unpriced today, 1 with no price on 2026-09-16"
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


def _markers(card):
    """[(short, hover)] of a card's markers (its third child), [] when it has none."""
    if len(card.children) < 3:
        return []
    return [(m.children, getattr(m, "title", None)) for m in card.children[2].children]


def _no_visible_sentences(node):
    """True when no element under `node` is a caption line (the pre-2026-09-25 visible
    sentences); markers and hovers only."""
    if "header-figure-caption" in str(getattr(node, "className", "") or ""):
        return False
    children = getattr(node, "children", None)
    return all(_no_visible_sentences(c) for c in (children if isinstance(children, (list, tuple)) else [children])
               if hasattr(c, "children"))


def test_pnl_card_unavailable_shows_na_with_its_reason_on_hover():
    """Screens redesign (user, 2026-09-25): no visible sentence; n/a carries its reason on hover."""
    card = header._pnl_card("X", {"available": False, "reason": "no mark"})
    assert len(card.children) == 2
    value_div = card.children[1]
    assert value_div.children == "n/a"
    assert value_div.title == "no mark"
    assert "header-figure-value--muted" in value_div.className


def test_pnl_card_unavailable_with_blank_reason_has_no_marker():
    card = header._pnl_card("X", {"available": False, "reason": ""})
    assert len(card.children) == 2


def test_pnl_card_available_is_short_money_with_the_full_figure_on_hover():
    card = header._pnl_card("X", {"value": 5.0, "available": True})
    assert len(card.children) == 2
    neg = header._pnl_card("X", {"value": -51_018.4, "available": True})
    assert neg.children[1].children == "−$51.0k"                  # a real minus, k / m
    assert neg.children[1].title == "-$51,018"                           # the full figure on hover
    assert "header-figure-value--neg" in neg.children[1].className
    assert header._pnl_card("X", {"value": 1_650_590.0, "available": True}).children[1].children == "$1.65m"


def test_pnl_card_available_with_partial_pricing_shows_an_excl_marker_with_the_sentence_on_hover():
    card = header._pnl_card("X", {
        "value": 100.0, "available": True,
        "excluded_summary": "excludes 1 of 2 trades unpriced",
        "excluded_detail": "1 option: no PREMIUM",
    })
    assert len(card.children) == 3
    assert card.children[1].children == "$100"  # a real number, not "n/a" -- this is a priced figure
    [(short, hover)] = _markers(card)
    assert short == "excl. 1"
    assert hover == "excludes 1 of 2 trades unpriced\n1 option: no PREMIUM"
    assert card.children[2].children[0].className == "marker"
    assert _no_visible_sentences(card)


# --------------------------------------------------------------------- _build_figures


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
    assert entry["excluded_summary"] == "excludes 1 of 3 trades: 0 unpriced today, 1 with no price on 2026-09-16"


def test_build_figures_daily_names_reference_date_when_yesterday_has_no_marks(strict_marks):
    """End to end: marks for today only, and no earlier close within 5 business days has
    any either (the trade was open on all of them). Daily's caption must talk about the
    reference date (t-1) and the backfill, not about today, which is fully priced."""
    conn = schema.connect()
    _insert_instrument(conn, "USDJPY", "FX", "USD", "JPY")
    _insert_trade(conn, "t1", "USDJPY", "FX_FWD", "2026-08-20", 1_000_000, 147.0)  # open on every close tried
    _insert_legs(conn, [
        ("t1", 1, "FX_NEAR", "USD", 1_000_000, "2026-08-20", "2026-09-30", 147.0, 1),
        ("t1", 2, "FX_NEAR", "JPY", -147_000_000, "2026-08-20", "2026-09-30", 147.0, 1),
    ])
    _insert_official_mark(conn, "2026-09-17", "USDJPY", "2026-09-17", "SPOT", 147.0, "BBG_BFXFORWARD")
    _insert_official_mark(conn, "2026-09-17", "USDJPY", "2026-09-30", "FWD_OUTRIGHT", 148.0, "BBG_BFXFORWARD")
    conn.commit()
    cards = header._build_figures(conn, "2026-09-17")
    by_title = {c.children[0].children: c for c in cards if getattr(c, "children", None) and c.children
                and hasattr(c.children[0], "children")}
    daily = by_title["Daily"]
    assert daily.children[1].children == "n/a"
    assert len(daily.children) == 2                                   # the sentence is on hover only
    caption = daily.children[1].title
    assert caption.startswith("Daily needs the 2026-09-16 close: 1 of 1 trades open that day")
    assert "backfill" in caption
    assert "2026-09-17" not in caption
    # an in-memory database has no status file: the "nothing known" sentence, and never an
    # instruction to run something no screen can run
    assert "needed marks) — the backfill fills past closes by itself after each Bloomberg pull" in caption
    assert "run the" not in caption and "Market data tab" not in caption
    # 2026-09-21: the earlier closes were tried first, and the caption says none has value
    # (5 business days before 2026-09-16)
    assert caption.endswith("No earlier close within 5 business days has one either (checked back to 2026-09-09).")


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

    reasons = ["no forward-curve history for USDCAD", "no PX_LAST history for CLZ6 Comdty on 2026-09-14",
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


# ------------------------------------------------- the whole history (2026-09-22)
# User decision 2026-09-22: "yes I want to see the ltd line chart, which requires all the
# previous closes". The chart used to show the last 20 business days; it now runs from the
# book's first trade date to as_of, one `_cached_ltd` evaluation per business day.


def test_build_chart_spans_every_business_day_from_the_first_trade(tmp_path, monkeypatch):
    import datetime as dt
    from engine.pnl.calendar import _is_business_day, _n_business_days_back, load_holidays

    as_of = "2026-09-17"
    holidays = load_holidays()
    first = _n_business_days_back(dt.date.fromisoformat(as_of), 60, holidays)  # over the 2026-09-07 holiday
    db = tmp_path / "risk.db"
    conn = schema.connect(db)
    _insert_instrument(conn, "USDJPY", "FX", "USD", "JPY")
    _insert_trade(conn, "t1", "USDJPY", "FX_FWD", first.isoformat(), 1_000_000, 147.0)
    _insert_trade(conn, "t2", "USDJPY", "FX_FWD", "2026-09-01", 1_000_000, 147.0)  # later: not the start
    _insert_legs(conn, [
        ("t1", 1, "FX_NEAR", "USD", 1_000_000, first.isoformat(), "2026-09-30", 147.0, 1),
        ("t1", 2, "FX_NEAR", "JPY", -147_000_000, first.isoformat(), "2026-09-30", 147.0, 1),
        ("t2", 1, "FX_NEAR", "USD", 1_000_000, "2026-09-01", "2026-09-30", 147.0, 1),
        ("t2", 2, "FX_NEAR", "JPY", -147_000_000, "2026-09-01", "2026-09-30", 147.0, 1),
    ])
    conn.commit()

    evaluated = []

    def cheap(db_path, _mtime, day):                    # stands in for the per-day value_book run
        evaluated.append(day)
        if day == "2026-09-01":
            return (None, 2, 2, 0)                      # nothing priced that day: a gap, kept
        return (float(len(evaluated)), 0, 2, 1 if day == as_of else 0)

    monkeypatch.setattr(header, "_cached_ltd", cheap)
    try:
        graph = header._build_chart(conn, as_of, db_path=db)
    finally:
        conn.close()
    trace = graph.figure["data"][0]
    xs = trace["x"]
    assert len(xs) == 61                                # 60 business days back, as_of included
    assert xs[0] == first.isoformat() and xs[-1] == as_of
    assert xs == sorted(xs)                             # oldest first
    assert all(_is_business_day(dt.date.fromisoformat(x), holidays) for x in xs)
    assert "2026-09-07" not in xs                       # Labor Day
    assert evaluated == xs                              # one evaluation per day, none outside the span
    gap = xs.index("2026-09-01")
    assert trace["y"][gap] is None and trace["text"][gap] == "nothing priced (2 trades)"
    assert trace["text"][-1] == "1 valued at an earlier close"
    layout = graph.figure["layout"]
    assert layout["xaxis"] == {"type": "date", "tickformat": "%d %b"}   # months of days read as dates
    assert layout["height"] == 260
    assert not hasattr(header, "_CHART_LOOKBACK_DAYS")  # the 20-day window is gone


def test_build_chart_with_no_trades_has_no_points_and_says_so(monkeypatch):
    """Nothing to chart before the book exists: no day is evaluated, and the reader sees a
    sentence, never a blank graph (CLAUDE.md: no figure is blank without its reason)."""
    from ui.tabs import blotter_pricing

    def never(*_args, **_kwargs):
        raise AssertionError("no day should be valued for an empty book")

    monkeypatch.setattr(header, "_cached_ltd", never)
    monkeypatch.setattr(blotter_pricing, "priced_value_book", never)
    conn = schema.connect()
    assert header._chart_days(conn, "2026-09-17") == []
    out = header._build_chart(conn, "2026-09-17")
    assert not hasattr(out, "figure")
    assert out.children == "No trades dated on or before 2026-09-17: nothing to chart."

    # a book whose first trade is after as_of is the same: nothing existed yet
    _insert_instrument(conn, "USDJPY", "FX", "USD", "JPY")
    _insert_trade(conn, "t1", "USDJPY", "FX_SPOT", "2026-09-18", 1_000_000, 147.0)
    conn.commit()
    assert header._chart_days(conn, "2026-09-17") == []
    import datetime as dt
    assert header._chart_days(conn, "2026-09-18") == [dt.date(2026, 9, 18)]


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
    assert ltd.children[1].title == f"${1_000_000 / 149.0 + 40_000 + 10_000:,.0f}"
    assert len(ltd.children) == 2  # fully priced: no "excl." marker


def test_fx_net_and_gross_usd_delta_left_the_header():
    """Screens redesign plan (user, 2026-09-25): FX Net / Gross USD delta live on the FX & cash
    tab's headline card only ("one place per number"); a text leg amount, which used to cost
    those two cards, leaves the header's P&L cards standing."""
    conn = _db_two_priced_forwards()
    conn.execute("UPDATE trade_legs SET amount = '24-Jul' WHERE trade_id = 'j1' AND leg_no = 1")
    conn.commit()
    cards = header._build_figures(conn, "2026-09-17")
    titles = [c.children[0].children for c in cards]
    assert "Net USD delta" not in titles and "Gross USD delta" not in titles
    assert _card(cards, "LTD").children[1].children != "n/a"


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


# ------------------------------------------------- the chart opens on a click (2026-09-22)
# User: "the LTD line chart not working". The chart callback is gated on the Details'
# `open` prop, and Dash's html bundle (4.4.1) never reports a native <details> toggle back
# to it: a click opened the element in the browser while the server never heard of it, so
# the container stayed empty (the server side was fine: a POST with open=true returned the
# graph). A clientside callback now mirrors the element's DOM state into `open` after each
# click on the summary. And a chart that cannot be built says why on the page instead of
# escaping as an HTTP 500 that left the opened collapsible blank.


def _app_with_header(tmp_path):
    import dash

    db = tmp_path / "risk.db"
    schema.connect(db).close()
    app = dash.Dash(__name__)
    header.register_callbacks(app, get_db_path=lambda: db)
    return app, db


# --------------------------------------------------------------------- the commodity strip
# Commodity conversion Phase 3 (2026-09-24): gross commodity notional, net outright by sector,
# open spreads, the next first notice / last trade, after the FX Net / Gross cards. Rendered
# from book-positions, spreads-engine and expiry-monitor as given, nothing recomputed.

_CMDTY_AS_OF = "2026-11-18"          # a Wednesday: COMEX copper's first notice (Fri 20th) is 2 business days away
_CMDTY_EXPIRY = {"CLZ26 Comdty": "2026-12-18", "CLF27 Comdty": "2027-01-20", "COZ26 Comdty": "2026-12-30",
                 "HGZ26 Comdty": "2026-12-29", "CUZ26 Comdty": "2026-12-15"}
_CMDTY_ROOT = {"CLZ26 Comdty": "NYMEX:CL", "CLF27 Comdty": "NYMEX:CL", "COZ26 Comdty": "ICE:B",
               "HGZ26 Comdty": "COMEX:HG", "CUZ26 Comdty": "SHFE:CU"}
_CMDTY_PX = {"CLZ26 Comdty": 71.0, "CLF27 Comdty": 70.2, "COZ26 Comdty": 74.5, "HGZ26 Comdty": 4.6,
             "CUZ26 Comdty": 80_000.0}


def _future(conn, tid, inst, lots, fill, account="ACC", trade_date="2026-09-01", price=True):
    from data.contracts import get_root
    root = get_root(_CMDTY_ROOT[inst])
    expiry = _CMDTY_EXPIRY[inst]
    conn.execute("INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, "
                 "is_ndf, bbg_ticker, expiry_date) VALUES (?,'FUTURE',?,?,?,0,?,?)",
                 (inst, root.root_id, root.currency, root.multiplier, inst, expiry))
    conn.execute("INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, "
                 "price, account, counterparty, strategy, trader, description) "
                 "VALUES (?, 'XLSX', ?, 'FUTURE', ?, ?, ?, ?, ?, 'C', '', 'JB', 'd')",
                 (tid, inst, tid, trade_date, lots, fill, account))
    conn.execute("INSERT INTO trade_legs VALUES (?,1,'NOTIONAL',?,?,?,?,?,0)",
                 (tid, root.currency, lots * root.multiplier * fill, trade_date, expiry, fill))
    if price:
        conn.execute("INSERT OR IGNORE INTO marks VALUES (?,?,?,'FUTURE_PX',?,'BBG_BDH',?)",
                     (_CMDTY_AS_OF, inst, expiry, _CMDTY_PX[inst], f"{_CMDTY_AS_OF}T17:00:00-05:00"))


def _bloomberg_dates(conn, inst, first_notice=""):
    from data.contracts import store_static_dates
    store_static_dates(conn, [{"contract_id": inst, "last_trade_date": _CMDTY_EXPIRY[inst],
                               "first_notice_date": first_notice, "source": "BBG_BDP"}])


def _commodity_book():
    """Two sectors (energy: a WTI calendar and a Brent / WTI pair on two accounts, which the
    rule sends to review; metals: COMEX copper two business days from its first notice), every
    contract priced on the day and on Bloomberg's dates."""
    conn = schema.connect()
    _future(conn, "W1", "CLZ26 Comdty", 2, 70.0)
    _future(conn, "W2", "CLF27 Comdty", -2, 69.5)
    _future(conn, "B1", "COZ26 Comdty", 5, 72.3, account="ONSHORE", trade_date="2026-09-02")
    _future(conn, "C1", "CLZ26 Comdty", -5, 68.6, account="OFFSHORE", trade_date="2026-09-02")
    _future(conn, "H1", "HGZ26 Comdty", 4, 4.5, trade_date="2026-09-03")
    for inst in ("CLZ26 Comdty", "CLF27 Comdty", "COZ26 Comdty"):
        _bloomberg_dates(conn, inst)
    _bloomberg_dates(conn, "HGZ26 Comdty", first_notice="2026-11-20")
    conn.commit()
    return conn


def _texts(card):
    return [getattr(ch, "children", None) for ch in card.children]


def test_a_book_with_no_commodity_futures_shows_one_plain_line_with_its_reason():
    conn = _db_two_priced_forwards()
    try:
        strip = header._commodity_cards(conn, "2026-09-17")
    finally:
        conn.close()
    assert len(strip) == 1
    assert _texts(strip[0]) == [header.COMMODITY_EMPTY_TITLE, "none"]
    assert strip[0].children[1].title == "no open commodity futures on 2026-09-17"


def test_unpriced_commodities_read_na_with_the_reason_and_the_other_cards_still_show():
    conn = schema.connect()
    _future(conn, "H1", "HGZ26 Comdty", 4, 4.5, price=False)       # no FUTURE_PX on the day
    _bloomberg_dates(conn, "HGZ26 Comdty", first_notice="2026-11-20")
    conn.commit()
    try:
        cards = header._commodity_cards(conn, _CMDTY_AS_OF)
    finally:
        conn.close()
    by_title = {c.children[0].children: c for c in cards}
    for title in (header.GROSS_NOTIONAL_TITLE, header.NET_BY_SECTOR_TITLE):
        value = by_title[title].children[1]
        assert value.children == "n/a" and "HG" in value.title          # never a zero standing for a missing price
        assert len(by_title[title].children) == 2                        # the reason on hover, not a caption
    assert _texts(by_title[header.OPEN_SPREADS_TITLE])[1] == "0"
    assert _texts(by_title[header.NEXT_EXPIRY_TITLE])[1].startswith("HGZ26 first notice")


def test_a_partly_priced_book_sums_the_known_figures_and_says_what_it_excludes():
    from engine.ladder.positions import book_positions
    conn = schema.connect()
    _future(conn, "H1", "HGZ26 Comdty", 4, 4.5)
    _future(conn, "S1", "CUZ26 Comdty", 1, 79_000.0)                 # a CNY future with no USDCNY spot on file
    conn.commit()
    try:
        block = book_positions(conn, _CMDTY_AS_OF)["commodities"]
        cards = header._commodity_cards(conn, _CMDTY_AS_OF)
    finally:
        conn.close()
    assert block["missing"] == ["SHFE:CU"] and block["gross_usd"] is not None
    gross = cards[0]
    assert _texts(gross)[1] == header.short_money(block["gross_usd"], "$")
    assert _markers(gross) == [("excl. 1", block["reason"])]
    assert block["reason"].startswith("excludes 1 of 2 commodities with no USD figure")
    net = cards[1]
    assert _markers(net) == [("excl. 1", block["reason"])]
    assert f"Metals {header._fmt_compact(block['net_usd'])}" in net.children[1].title   # one sector, CU out of it


def test_open_spreads_that_cannot_be_grouped_say_why_and_cost_only_their_card(monkeypatch):
    import engine.spreads

    def boom(*_args, **_kwargs):
        raise RuntimeError("template file unreadable")

    monkeypatch.setattr(engine.spreads, "book_spreads", boom)
    conn = _commodity_book()
    try:
        cards = header._commodity_cards(conn, _CMDTY_AS_OF)
    finally:
        conn.close()
    by_title = {c.children[0].children: c for c in cards}
    card = by_title[header.OPEN_SPREADS_TITLE]
    assert card.children[1].children == "n/a"
    assert card.children[1].title.startswith("spreads could not be grouped (RuntimeError: template file unreadable)")
    assert _texts(by_title[header.NEXT_EXPIRY_TITLE])[1].startswith("HGZ26 first notice")


def test_open_spreads_are_worked_out_once_per_database_revision(tmp_path, monkeypatch):
    import os

    import engine.spreads
    db = tmp_path / "risk.db"
    schema.connect(db).close()
    calls = []

    def counting(conn, as_of, **_kwargs):
        calls.append(as_of)
        return {"spreads": [], "positions": [], "review": [], "reasons": []}

    monkeypatch.setattr(engine.spreads, "book_spreads", counting)
    monkeypatch.setattr(header, "_SPREADS_MEMO", {})
    conn = sqlite3.connect(db)
    try:
        header._spread_summary(conn, _CMDTY_AS_OF)
        header._spread_summary(conn, _CMDTY_AS_OF)
        assert calls == [_CMDTY_AS_OF]                                # the second render reads the memo
        stat = os.stat(db)
        os.utime(db, (stat.st_atime, stat.st_mtime + 5))              # the database changed
        header._spread_summary(conn, _CMDTY_AS_OF)
        assert len(calls) == 2
    finally:
        conn.close()


def test_next_expiry_card_shows_expired_and_estimated_dates():
    row = {"contract_id": "CLQ26 Comdty", "next_event": "last trade", "next_event_date": "2026-08-31",
           "alert_date": "2026-07-01", "alert_basis": "estimated: first business day of Jul 2026",
           "business_days": -55, "estimated": True, "level": "EXPIRED", "reason": "delivery risk, close now"}
    later = {"contract_id": "CUX26 Comdty", "next_event": "last trade", "next_event_date": "2026-11-30",
             "alert_date": "2026-10-01", "business_days": 9, "estimated": True, "level": "AMBER", "reason": ""}
    card = header._next_expiry_card({"rows": [row, later], "counts": {"EXPIRED": 1, "RED": 0, "AMBER": 1, "GREEN": 0},
                                     "settled_expired": [{"contract_id": "CLN26 Comdty"}]})
    assert _texts(card)[:2] == [header.NEXT_EXPIRY_TITLE, "CLQ26 last trade · expired"]
    [(short, est_hover)] = _markers(card)
    assert short == "est." and est_hover.startswith("2026-08-31 is contract-master's estimate")
    style = card.children[1].style
    assert style["color"] == "#ffffff" and style["backgroundColor"] == header._LEVEL_STYLES["EXPIRED"]["backgroundColor"]
    hover = card.children[1].title
    assert hover.startswith("CLQ26 Comdty: last trade 2026-08-31 (estimated), EXPIRED\n")
    assert "delivery risk, close now" in hover and "1 EXPIRED" in hover
    assert "CUX26 Comdty: last trade 2026-11-30 (est.), alert 2026-10-01, in 9 business days, AMBER" in hover
    assert "1 expired contract settled by the ledger: not alerts" in hover


def test_compact_money_and_sector_labels():
    assert header._fmt_compact(12_345_678) == "+12.3m"
    assert header._fmt_compact(-4_100_000) == "−4.1m"
    assert header._fmt_compact(999_960) == "+1.0m"
    assert header._fmt_compact(812.4) == "+812"
    assert header._fmt_compact(None) == "n/a" and header._fmt_compact(float("nan")) == "n/a"
    assert header._sector_label("agriculture") == "Ags" and header._sector_label("energy") == "Energy"


# --------------------------------------------------------------------- the Data chip (2026-09-25)
# Screens redesign plan: a chip for the marks the book needs on the as-of date that have no
# official mark, from `needed_marks` (the list the Data tab shows), read once per render.


def test_marks_chip_counts_the_missing_marks_with_the_list_on_hover(monkeypatch):
    import datetime as dt
    from data.bloomberg import live

    monkeypatch.setattr(live, "book_today", lambda: dt.date(2026, 9, 17))   # today: the live request list
    conn = _db_with_one_open_fx_trade()
    card = header._marks_card(conn, "2026-09-17", None)
    assert card.children[0].children == header.MARKS_TITLE
    assert card.children[1].children == "2 marks missing"
    assert card.children[1].style["color"] == header._LEVEL_STYLES["AMBER"]["color"]
    hover = card.children[1].title
    assert hover.startswith("2 of 2 marks the book needs on 2026-09-17 have no official mark (1 FWD_OUTRIGHT, 1 SPOT).")
    assert "- USDJPY SPOT 2026-09-17" in hover and "The Data tab lists each one" in hover

    _insert_official_mark(conn, "2026-09-17", "USDJPY", "2026-09-17", "SPOT", 147.0, "BBG_BFXFORWARD")
    conn.commit()
    assert header._marks_card(conn, "2026-09-17", None).children[1].children == "1 mark missing"
    _insert_official_mark(conn, "2026-09-17", "USDJPY", "2026-09-17", "FWD_OUTRIGHT", 148.0, "BBG_BFXFORWARD")
    conn.commit()
    done = header._marks_card(conn, "2026-09-17", None)
    assert done.children[1].children == "marks complete"
    assert done.children[1].title == "every one of the 2 marks the book needs on 2026-09-17 is on file (official)"


def test_marks_chip_is_left_out_when_the_book_needs_no_mark_and_says_why_when_it_cannot_list(monkeypatch):
    conn = schema.connect()
    assert header._marks_card(conn, "2026-09-17", None) is None
    titles = [c.children[0].children for c in header._build_figures(conn, "2026-09-17")]
    assert header.MARKS_TITLE not in titles

    def boom(*_args, **_kwargs):
        raise RuntimeError("inventory unreadable")

    monkeypatch.setattr(header, "needed_marks", boom)
    card = header._marks_card(conn, "2026-09-17", None)
    assert card.children[1].children == "marks n/a"
    assert card.children[1].title.startswith("the marks the book needs on 2026-09-17 could not be listed "
                                             "(RuntimeError: inventory unreadable)")
    header._build_figures(conn, "2026-09-17")                           # the P&L cards still build


def test_the_needed_marks_are_read_once_per_database_revision(tmp_path, monkeypatch):
    import os

    db = tmp_path / "risk.db"
    schema.connect(db).close()
    calls = []

    def counting(conn, as_of):
        calls.append(as_of)
        return (3, [{"instrument_id": "X", "settle_date": as_of, "mark_type": "SPOT"}])

    monkeypatch.setattr(header, "needed_marks", counting)
    monkeypatch.setattr(header, "_NEEDS_MEMO", {})

    def as_of_reads():   # the reference closes' reasons read their own dates, as before
        return calls.count("2026-09-17")

    conn = sqlite3.connect(db)
    try:
        cards = header._build_figures(conn, "2026-09-17")
        assert as_of_reads() == 1                          # the LTD reason and the chip share one read
        assert _card(cards, header.MARKS_TITLE).children[1].children == "1 mark missing"
        header._build_figures(conn, "2026-09-17")
        assert as_of_reads() == 1                          # the next render reads the memo
        stat = os.stat(db)
        os.utime(db, (stat.st_atime, stat.st_mtime + 5))   # the database changed
        header._build_figures(conn, "2026-09-17")
        assert as_of_reads() == 2
    finally:
        conn.close()


# --------------------------------------------------------------------- the risk chip (Phase B)
# Screens redesign plan, Phase B (user, 2026-09-25): the book's VaR against the vol target,
# risk-metrics' `book_risk` as given, worked out off the figures' render and memoised.

_NAN = float("nan")


def _risk_result(var=33_571.03, vol=301_988.63, pct=6.7108, over=False, reasons=None, parts=None,
                 in_series=("NYMEX:CL",), placeholder=True):
    """A `book_risk`-shaped result carrying the keys the chip reads."""
    parts = parts if parts is not None else [
        {"underlyer": "NYMEX:CL", "kind": "COMMODITY", "role": "part", "reason": "",
         "contracts": [{"contract_id": "CLZ26 Comdty", "in_series": True},
                       {"contract_id": "CLZ26C 75 Comdty", "in_series": False,
                        "reason": "no DELTA mark: the option has not been priced"}]},
        {"underlyer": "EUR", "kind": "FX", "role": "part", "reason": "no market history: no folder"},
        {"underlyer": "JPY", "kind": "FX", "role": "part", "reason": "no market history: no folder"},
        {"underlyer": "energy", "kind": "SECTOR", "role": "view", "reason": ""},
    ]
    return {
        "as_of": "2026-09-18",
        "book": {"var95_1d_usd": var, "vol_blended_ann_usd": vol, "vol_trailing_ann_usd": vol,
                 "vol_crisis_ann_usd": vol, "vol_vs_target_pct": pct, "over_vol_target": over,
                 "vol_note": "crisis window not in history: trailing vol only", "lag2_date": "2026-09-16",
                 "reason": "", "reasons": reasons or {}, "rows_in_series": list(in_series)},
        "config": {"vol_target_usd": 4_500_000.0, "vol_target_placeholder": placeholder,
                   "vol_target_note": "placeholder: the macro fund's 4.5m, until Jason sets his own vol target",
                   "var_window_bd": 252, "var_confidence": 0.95,
                   "blended": {"trail_window_bd": 500, "w_trail": 0.6666666667, "w_stress": 0.3333333333,
                               "stress_start": "2008-01-01", "stress_end": "2010-12-31"}},
        "underlyers": parts,
    }


def _chip(result):
    return header._risk_card(header._slim_risk(result))


def test_risk_chip_shows_the_var_and_the_vol_share_of_target_as_risk_metrics_gives_them():
    card = _chip(_risk_result())
    assert card.id == header.VAR_CHIP_ID
    assert card.children[0].children == header.RISK_TITLE
    value = card.children[1]
    assert value.children == "VaR " + header.short_money(33_571.03, "$") == "VaR $33.6k"
    marks = dict(_markers(card))
    assert list(marks) == ["excl. 3", "6.7% of target"]
    assert marks["6.7% of target"].startswith("blended annual vol $301,989 is 6.7% of the vol target ($4,500,000")
    assert not getattr(card.children[2].children[1], "style", None)   # not over: a plain marker
    # the definitions and the target on hover, the target said to be a placeholder
    assert "1y 95% VaR (1-day): $33,571." in value.title
    assert "5th percentile of the book's last 252 daily $ P&Ls" in value.title
    assert "2/3 x trailing 500-day vol + 1/3 x crisis vol (2008-01-01 to 2010-12-31)" in value.title
    assert "crisis window not in history: trailing vol only" in value.title
    assert "Vol target: $4,500,000, a placeholder: the macro fund's 4.5m" in value.title
    assert _no_visible_sentences(card)


def test_risk_chip_says_what_the_book_series_leaves_out():
    card = _chip(_risk_result())
    hover = dict(_markers(card))["excl. 3"]
    assert hover.startswith("The VaR and the vol sum the positions that have a history series: they exclude "
                            "2 underlyers and 1 contract with none.")
    assert "- EUR, JPY: no market history: no folder" in hover                      # one line per reason
    assert "- CLZ26C 75 Comdty: no DELTA mark: the option has not been priced" in hover
    assert "energy" not in hover                                                    # a view is never a part
    clean = _chip(_risk_result(parts=[{"underlyer": "NYMEX:CL", "kind": "COMMODITY", "role": "part",
                                       "contracts": [{"contract_id": "CLZ26 Comdty", "in_series": True}]}]))
    assert [m for m, _ in _markers(clean)] == ["6.7% of target"]


def test_risk_chip_colours_the_vol_marker_when_over_the_target():
    card = _chip(_risk_result(vol=5_400_000.0, pct=120.0, over=True))
    marker_span = card.children[2].children[1]
    assert marker_span.children == "120% of target"
    assert marker_span.style == header._OVER_TARGET_MARKER
    assert marker_span.title.endswith(": over the target")
    assert "(over the target)" in card.children[1].title


def test_risk_chip_without_a_placeholder_target_does_not_call_it_one():
    card = _chip(_risk_result(placeholder=False))
    assert "Vol target: $4,500,000." in card.children[1].title
    assert "placeholder" not in card.children[1].title


def test_risk_chip_with_no_var_reads_na_with_its_reason_never_zero():
    why = "no market history: no market history folder: tried a, b"
    card = _chip(_risk_result(var=_NAN, vol=_NAN, pct=_NAN, reasons={"all": why}, in_series=()))
    value = card.children[1]
    assert value.children == "VaR n/a"
    assert value.title.startswith(f"1y VaR n/a: {why}")
    assert "Vol target: $4,500,000, a placeholder" in value.title
    assert len(card.children) == 2                                     # no excl. and no % beside an n/a


def test_risk_chip_with_a_var_but_no_vol_says_so():
    card = _chip(_risk_result(vol=_NAN, pct=_NAN,
                              reasons={"vol_blended_ann_usd": "fewer than 500 observations (300)"}))
    marks = dict(_markers(card))
    assert marks["vol n/a"] == "blended annual vol n/a: fewer than 500 observations (300)"
    assert card.children[1].children == "VaR $33.6k"


def test_risk_chip_pending_and_error_states_carry_their_reason():
    pending = header._risk_card(None)
    assert pending.id == header.VAR_CHIP_ID
    assert pending.children[1].children == "VaR …"
    assert "being worked out" in pending.children[1].title
    failed = header._risk_card({"error": "the book's risk could not be computed (RuntimeError: boom)"})
    assert failed.children[1].children == "VaR n/a"
    assert failed.children[1].title == "the book's risk could not be computed (RuntimeError: boom)"


def _risk_db(tmp_path):
    db = tmp_path / "risk.db"
    schema.connect(db).close()
    return db


def _count_book_risk(monkeypatch, result=None, exc=None):
    import engine.risk

    calls = []

    def fake(conn, as_of, **kwargs):
        calls.append((as_of, sorted(kwargs)))
        if exc is not None:
            raise exc
        return result if result is not None else _risk_result()

    monkeypatch.setattr(engine.risk, "book_risk", fake)
    monkeypatch.setattr(header, "_RISK_MEMO", {})
    monkeypatch.setattr(header, "_RISK_LATEST", {})
    return calls


def test_risk_summary_is_memoised_on_the_database_revision(tmp_path, monkeypatch):
    import os

    calls = _count_book_risk(monkeypatch)
    db = _risk_db(tmp_path)
    conn = sqlite3.connect(db)
    try:
        first = header.risk_summary(conn, "2026-09-18")
        assert header.risk_summary(conn, "2026-09-18") is first and len(calls) == 1
        stat = os.stat(db)
        os.utime(db, (stat.st_atime, stat.st_mtime + 5))                       # the database changed
        header.risk_summary(conn, "2026-09-18")
        assert len(calls) == 2
    finally:
        conn.close()


def test_the_public_risk_summary_if_ready_is_the_private_one_for_other_screens(tmp_path, monkeypatch):
    calls = _count_book_risk(monkeypatch)
    conn = sqlite3.connect(_risk_db(tmp_path))
    try:
        assert header.risk_summary_if_ready(conn, "2026-09-18") is None and calls == []   # never computes
        done = header.risk_summary(conn, "2026-09-18")
        assert header.risk_summary_if_ready(conn, "2026-09-18") is done is header._risk_summary_if_ready(conn, "2026-09-18")
    finally:
        conn.close()


def test_risk_summary_failure_says_why_and_is_tried_again(tmp_path, monkeypatch):
    calls = _count_book_risk(monkeypatch, exc=RuntimeError("history unreadable"))
    db = _risk_db(tmp_path)
    conn = sqlite3.connect(db)
    try:
        out = header.risk_summary(conn, "2026-09-18")
        assert out["error"].startswith("the book's risk could not be computed (RuntimeError: history unreadable)")
        chip = header._risk_card(out)
        assert chip.children[1].children == "VaR n/a" and "history unreadable" in chip.children[1].title
        header.risk_summary(conn, "2026-09-18")
        assert len(calls) == 2                                                  # a failure is not memoised
        assert header._risk_summary_if_ready(conn, "2026-09-18") is None
    finally:
        conn.close()


def test_open_spreads_count_positions_not_trade_dates_on_the_sample_book(monkeypatch):
    """The sample's CL Z26/F27 calendar was put on twice (two trade dates): one position, so the
    card reads the positions' count (5), not the spreads' (6), with each position's name on
    hover (screens redesign plan, "One place per number": the Spreads tab shows positions)."""
    from engine.spreads import book_spreads
    from tests.golden_book import build_book
    from ui.tabs.blotter_pricing import priced_value_book
    monkeypatch.setattr(header, "_SPREADS_MEMO", {})
    conn = schema.connect()
    try:
        build_book(conn)
        as_of = "2026-09-18"
        out = book_spreads(conn, as_of, value_fn=priced_value_book)
        open_positions = [p for p in out["positions"] if p["status"] == "open"]
        assert len(open_positions) == 5
        assert sum(1 for s in out["spreads"] if s["status"] == "open") == 6
        card = header._open_spreads_card(header._spread_summary(conn, as_of))
    finally:
        conn.close()
    assert _texts(card)[1] == "5"
    hover = card.children[1].title
    for p in open_positions:
        assert p["name"] in hover
    cl = next(p for p in open_positions if p["kind"] == "calendar")
    assert f"{cl['name']} {cl['direction']} (2 entries, traded {', '.join(cl['trade_dates'])})" in hover
    assert [short for short, _h in _markers(card)] == ([f"review {len(out['review'])}"] if out["review"] else [])


def test_open_spreads_leave_closed_positions_out_and_label_each_open_one(monkeypatch):
    import engine.spreads

    def fake(conn, as_of, **_kwargs):
        return {"spreads": [{"status": "open"}] * 4,               # the per-trade-date list is not counted
                "positions": [
                    {"position_id": "P1", "name": "CL Z26/F27", "direction": "long", "status": "open",
                     "spread_ids": ["S1", "S2"], "trade_dates": ["2026-09-01", "2026-09-02"]},
                    {"position_id": "P2", "name": "Brent/WTI", "direction": "short", "status": "open",
                     "spread_ids": ["S3"], "trade_dates": ["2026-09-03"]},
                    {"position_id": "P3", "name": "Crack 3-2-1", "direction": "long", "status": "closed",
                     "spread_ids": ["S4"], "trade_dates": ["2026-09-04"]}],
                "review": [{"reason": "ratio 55 % off"}], "reasons": []}

    monkeypatch.setattr(engine.spreads, "book_spreads", fake)
    monkeypatch.setattr(header, "_SPREADS_MEMO", {})
    conn = schema.connect()
    try:
        summary = header._spread_summary(conn, _CMDTY_AS_OF)
    finally:
        conn.close()
    assert summary["open"] == ["CL Z26/F27 long (2 entries, traded 2026-09-01, 2026-09-02)",
                               "Brent/WTI short (traded 2026-09-03)"]
    card = header._open_spreads_card(summary)
    assert _texts(card)[1] == "2"
    assert "Crack 3-2-1" not in card.children[1].title
    [(short, review_hover)] = _markers(card)
    assert short == "review 1" and "ratio 55 % off" in review_hover
