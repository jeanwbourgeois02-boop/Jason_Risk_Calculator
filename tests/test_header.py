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


def test_build_figures_trades_card_is_always_present_with_zero_marks():
    conn = _db_with_one_open_fx_trade()
    cards = header._build_figures(conn, "2026-09-17")
    titles = [c.children[0].children for c in cards if getattr(c, "className", "") != "header-divider"]
    assert "Trades" in titles
    trades_card = next(c for c in cards if getattr(c, "children", None) and
                        getattr(c.children[0], "children", None) == "Trades")
    assert trades_card.children[1].children == "1"
    assert trades_card.children[1].title == "1 open, 0 settled"            # small, the split on hover
    assert len(trades_card.children) == 2


def test_build_figures_ltd_reason_is_actionable_when_marks_are_missing():
    conn = _db_with_one_open_fx_trade()
    cards = header._build_figures(conn, "2026-09-17")
    ltd_card = cards[0]
    assert ltd_card.children[0].children == "LTD"
    assert ltd_card.children[1].children == "n/a"
    reason = ltd_card.children[1].title
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
    [(short, hover)] = _markers(ltd_card)
    assert short == "excl. 1"
    assert hover.startswith("excludes 1 of 2 trades unpriced\n")
    assert "no PREMIUM" in hover
    assert "option" in hover


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


def test_build_figures_period_steps_back_to_the_previous_close_that_has_value(strict_marks):
    """User decision 2026-09-21 ("use previous date until has value", then "there should be a
    fill when bloomberg doesnt have the data"): the 2026-09-16 close has no marks, the
    2026-09-15 close has, so the trade is valued there at its 2026-09-15 close, Daily is
    measured from it and the card says so, with the reason on hover. No mark is written or
    copied: `marks` is untouched and `value_book` itself still has the 2026-09-16 row unpriced."""
    conn = schema.connect()
    _insert_instrument(conn, "USDJPY", "FX", "USD", "JPY")
    _insert_trade(conn, "t1", "USDJPY", "FX_FWD", "2026-08-20", 1_000_000, 147.0)
    _insert_legs(conn, [
        ("t1", 1, "FX_NEAR", "USD", 1_000_000, "2026-08-20", "2026-09-30", 147.0, 1),
        ("t1", 2, "FX_NEAR", "JPY", -147_000_000, "2026-08-20", "2026-09-30", 147.0, 1),
    ])
    for day, fwd in (("2026-09-15", 147.5), ("2026-09-17", 148.0)):
        _insert_official_mark(conn, day, "USDJPY", day, "SPOT", 147.0, "BBG_BFXFORWARD")
        _insert_official_mark(conn, day, "USDJPY", "2026-09-30", "FWD_OUTRIGHT", fwd, "BBG_BFXFORWARD")
    conn.commit()
    marks_before = conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0]
    cards = header._build_figures(conn, "2026-09-17")
    by_title = {c.children[0].children: c for c in cards if getattr(c, "children", None) and c.children
                and hasattr(c.children[0], "children")}
    daily = by_title["Daily"]
    # 1m x (148.0 - 147.5) JPY at spot 147
    assert daily.children[1].title == header._fmt_usd(1_000_000 * 0.5 / 147.0)
    [(short, hover)] = _markers(daily)
    assert short == "filled 1"
    assert hover.startswith("1 trade with no price on 2026-09-16 measured from its last earlier "
                            "close (back to 2026-09-15)\n")
    assert "t1: no price on 2026-09-16: value of the 2026-09-15 close (no FWD_OUTRIGHT mark for USDJPY" \
        in hover                                                                        # which close, and why, on hover
    assert conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0] == marks_before
    from engine.pnl.valuation import value_book
    assert (value_book(conn, "2026-09-16")["reason"] != "").all()


def test_build_figures_a_stepped_back_period_shows_ref_and_filled_markers(strict_marks):
    """Marks on 2026-09-17 and 2026-09-08 only. The 2026-09-16 close cannot be filled (the
    fill reaches 5 business days back, to 2026-09-09), so Daily steps back to 2026-09-15,
    whose fill reaches 2026-09-08: "ref 15 Sep" and "filled 1", each with its sentence on
    hover, and no visible sentence."""
    conn = schema.connect()
    _insert_instrument(conn, "USDJPY", "FX", "USD", "JPY")
    _insert_trade(conn, "t1", "USDJPY", "FX_FWD", "2026-08-20", 1_000_000, 147.0)
    _insert_legs(conn, [
        ("t1", 1, "FX_NEAR", "USD", 1_000_000, "2026-08-20", "2026-09-30", 147.0, 1),
        ("t1", 2, "FX_NEAR", "JPY", -147_000_000, "2026-08-20", "2026-09-30", 147.0, 1),
    ])
    for day, fwd in (("2026-09-08", 147.5), ("2026-09-17", 148.0)):
        _insert_official_mark(conn, day, "USDJPY", day, "SPOT", 147.0, "BBG_BFXFORWARD")
        _insert_official_mark(conn, day, "USDJPY", "2026-09-30", "FWD_OUTRIGHT", fwd, "BBG_BFXFORWARD")
    conn.commit()
    cards = header._build_figures(conn, "2026-09-17")
    daily = _card(cards, "Daily")
    assert daily.children[1].title == header._fmt_usd(1_000_000 * 0.5 / 147.0)
    markers = dict(_markers(daily))
    assert set(markers) == {"filled 1", "ref 15 Sep"}
    assert markers["ref 15 Sep"].startswith("from the 2026-09-15 close: 2026-09-16 has no usable close")
    assert "Daily needs the 2026-09-16 close" in markers["ref 15 Sep"]           # the skipped date's reason
    assert markers["filled 1"].startswith("1 trade with no price on 2026-09-15 measured from its last earlier "
                                          "close (back to 2026-09-08)\n")
    assert "t1: no price on 2026-09-15: value of the 2026-09-08 close" in markers["filled 1"]
    assert all(_no_visible_sentences(c) for c in cards)


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


def test_build_figures_survives_a_text_price_and_says_which_trade_and_column():
    conn = _db_two_priced_forwards()
    conn.execute("UPDATE trades SET price = '24-Jul' WHERE trade_id = 'j1'")
    conn.commit()
    cards = header._build_figures(conn, "2026-09-17")
    ltd = _card(cards, "LTD")
    assert ltd.children[1].title == "$40,000"  # e1 alone: 2,000,000 x (1.12 - 1.10); j1 contributes nothing
    assert ltd.children[1].children == "$40.0k"
    [(short, hover)] = _markers(ltd)
    assert short == "excl. 1"
    assert hover.startswith("excludes 1 of 2 trades unpriced (1 with a stored value is not a number)\n")
    assert "trade j1: trades.price is not a number ('24-Jul')" in hover
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


def test_layout_summary_carries_its_id_inside_the_details():
    root = header.layout()
    details = next(c for c in root.children if getattr(c, "id", None) == header.DETAILS_ID)
    assert type(details).__name__ == "Details" and details.open is False      # collapsed by default, as before
    summary = details.children[0]
    assert type(summary).__name__ == "Summary"
    assert summary.id == header.SUMMARY_ID
    assert summary.children == "LTD chart"                                   # compact (2026-09-25)
    assert header.SUMMARY_ID.startswith(header.DETAILS_ID) and header.SUMMARY_ID != header.DETAILS_ID


def test_register_callbacks_mirrors_the_summary_click_into_the_details_open_prop(tmp_path):
    app, _db = _app_with_header(tmp_path)
    key = f"{header.DETAILS_ID}.open"
    assert key in app.callback_map, list(app.callback_map)
    spec = app.callback_map[key]
    assert [(d["id"], d["property"]) for d in spec["inputs"]] == [(header.SUMMARY_ID, "n_clicks")]
    entry = next(c for c in app._callback_list if c["output"] == key)
    fn = entry["clientside_function"]
    assert fn is not None                                          # runs in the browser, not on the server
    script = next(s for s in app._inline_scripts if fn["function_name"] in s)
    assert f"getElementById('{header.DETAILS_ID}')" in script      # reads the element's own DOM state
    assert "details.open" in script
    assert "no_update" in script                                   # the initial call (n_clicks 0/null) mirrors nothing
    # the server callback still runs off `open`, exactly as before: the mirror feeds it
    chart = app.callback_map[f"{header.CHART_CONTAINER_ID}.children"]
    assert (header.DETAILS_ID, "open") in {(d["id"], d["property"]) for d in chart["inputs"]}
    assert chart["callback"] is not None


def test_chart_callback_says_why_when_the_chart_cannot_be_built(tmp_path, monkeypatch):
    app, _db = _app_with_header(tmp_path)

    def boom(*_args, **_kwargs):
        raise RuntimeError("no calendar file")

    monkeypatch.setattr(header, "_build_chart", boom)
    raw = app.callback_map[f"{header.CHART_CONTAINER_ID}.children"]["callback"].__wrapped__
    out = raw(True, "2026-09-17", None)                            # open, a date: the chart is built... and fails
    assert type(out).__name__ == "P"
    assert out.children.startswith("LTD chart could not be built (RuntimeError: no calendar file)")
    assert "header-figure-caption" in out.className
    # collapsed, or no date yet: nothing computed and nothing raised, as before
    assert type(raw(False, "2026-09-17", None)).__name__ == "NoUpdate"
    assert type(raw(True, None, None)).__name__ == "NoUpdate"


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


def test_commodity_strip_shows_the_four_engine_figures_as_given():
    from engine.expiry import expiry_schedule
    from engine.ladder.positions import book_positions
    from engine.spreads import book_spreads
    conn = _commodity_book()
    try:
        block = book_positions(conn, _CMDTY_AS_OF)["commodities"]
        assert {s["sector"] for s in block["sectors"]} == {"energy", "metals"} and block["reason"] == ""
        cards = header._build_figures(conn, _CMDTY_AS_OF)

        gross = _card(cards, header.GROSS_NOTIONAL_TITLE)
        assert _texts(gross)[1] == header.short_money(block["gross_usd"], "$") and len(gross.children) == 2
        assert gross.children[1].title.startswith(header._fmt_usd(block["gross_usd"]) + "\n")
        assert "Energy: net" in gross.children[1].title and "Metals: net" in gross.children[1].title
        assert "header-figure-value--neutral" in gross.children[1].className

        net = _card(cards, header.NET_BY_SECTOR_TITLE)
        assert _texts(net)[1] == header.short_money(block["net_usd"], "$") and len(net.children) == 2
        expected_line = " · ".join(f"{header._sector_label(s['sector'])} {header._fmt_compact(s['net_usd'])}"
                                   for s in block["sectors"])   # each sector's own net_usd, in book-positions' order
        assert net.children[1].title.split("\n")[:2] == [header._fmt_usd(block["net_usd"]), expected_line]

        spreads = book_spreads(conn, _CMDTY_AS_OF)
        assert sum(1 for s in spreads["spreads"] if s["status"] == "open") == 1 and len(spreads["review"]) == 1
        card = _card(cards, header.OPEN_SPREADS_TITLE)
        assert _texts(card)[1] == "1"
        [(short, review_hover)] = _markers(card)
        assert short == "review 1" and "1 group waiting for review" in review_hover and "Brent" in review_hover
        hover = card.children[1].title
        assert "CL Z26/F27" in hover and "waiting for review" in hover and "Brent" in hover

        first = expiry_schedule(conn, _CMDTY_AS_OF)["rows"][0]
        assert (first["contract_id"], first["next_event"], first["level"], first["business_days"]) == \
            ("HGZ26 Comdty", "first notice", "RED", 2)
        card = _card(cards, header.NEXT_EXPIRY_TITLE)
        assert _texts(card)[1] == "HGZ26 first notice · 2 bd" and len(card.children) == 2   # Bloomberg's dates: no "est."
        assert card.children[1].style["color"] == header._LEVEL_STYLES["RED"]["color"]
        assert "HGZ26 Comdty: first notice 2026-11-20, RED, in 2 business days" in card.children[1].title
        assert all(_no_visible_sentences(c) for c in cards)
    finally:
        conn.close()


def test_the_slim_header_order_has_no_fx_cards_and_ends_with_the_data_chip():
    conn = _db_two_priced_forwards()
    try:
        cards = header._build_figures(conn, "2026-09-17")
    finally:
        conn.close()
    titles = [c.children[0].children if getattr(c, "className", "") != "header-divider" else "|" for c in cards]
    assert titles == ["LTD", "Daily", "Previous day", "5d", "MTD", "YTD", "Trading", "Trades", "|",
                      header.COMMODITY_EMPTY_TITLE, header.MARKS_TITLE]
    for card in cards:
        if getattr(card, "className", "") == "header-divider":
            continue
        assert len(card.children) <= 3                                     # title, value, markers
        assert card.style["display"] == "grid"                              # markers beside the value, one row
        assert card.children[0].style == {"gridColumn": "1 / -1"}


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
        return {"spreads": [], "review": [], "reasons": []}

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
