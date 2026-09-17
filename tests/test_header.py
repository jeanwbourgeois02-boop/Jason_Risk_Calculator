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
