"""ui/tabs/header.py: the P&L header shown on every tab (docs/BUILD_PLAN.md section 5).

Covers the 2026-09-17 fix for the "the headline ... doesn't work" complaint: every
call into `_build_figures`/`_pnl_card` was already computing correctly on a database
with zero official marks (Net/Gross USD delta, trade counts) -- the bug was that the
*reason* for an unavailable P&L figure was only ever an HTML `title` tooltip, invisible
until hovered, and generic ("today's LTD unavailable") rather than actionable. These
tests pin the fix: a visible caption line, and a concrete "no official <mark_type> for
<date> (<n> of <m> needed marks) -- run the Bloomberg pull" sentence built from
`data.bloomberg.inventory.mark_inventory`.
"""
from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("dash", reason="dash is not installed in this environment")

from data.ingest import schema  # noqa: E402
from ui.tabs import header  # noqa: E402


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


# --------------------------------------------------------------------- _resolve_reason


def test_resolve_reason_substitutes_todays_ltd_reason():
    entry = {"available": False, "reason": "today's LTD unavailable"}
    out = header._resolve_reason(entry, "no official SPOT for 2026-09-17 — run the Bloomberg pull",
                                  lambda d: "should not be called")
    assert out == "no official SPOT for 2026-09-17 — run the Bloomberg pull"


def test_resolve_reason_substitutes_a_dated_ltd_reason():
    entry = {"available": False, "reason": "LTD on 2026-09-10 unavailable"}
    calls = []

    def missing_reason_for(date):
        calls.append(date)
        return f"no official SPOT for {date} — run the Bloomberg pull"

    out = header._resolve_reason(entry, "irrelevant", missing_reason_for)
    assert calls == ["2026-09-10"]
    assert out == "no official SPOT for 2026-09-10 — run the Bloomberg pull"


def test_resolve_reason_leaves_other_reasons_untouched():
    entry = {"available": False, "reason": "a trade dated today has no mark from any source"}
    out = header._resolve_reason(entry, "x", lambda d: "y")
    assert out == "a trade dated today has no mark from any source"


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
