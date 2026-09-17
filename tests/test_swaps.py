"""Tests for data/ingest/swaps.py (CLAUDE.md "package_id rule (FX swaps)").

Extracted 2026-09-17 from tests/test_ingest.py when data/ingest/bnp.py (the retired BNP
CSV parser) was deleted ("no bnp fall back", docs/bnp-excel-removal.md): these tests
used to build their fixture trades by running `bnp.load` on a synthetic BNP-shaped CSV.
`data/ingest/swaps.py` itself has no dependency on any parser -- it reads `trades` /
`trade_legs` directly -- so the fixtures below build those rows with plain SQL instead.
The happy-path case (grouping via the live blotter loader) is covered separately by
tests/test_blotter.py::test_load_writes_to_db_and_swap_packaging_still_works; this file
covers `package_swaps`'s own edge cases (round trips, ambiguous candidates, idempotency,
cross-source exclusion, crosses with no USD leg).
"""
from __future__ import annotations

import pytest

from data.ingest import schema, swaps

AS_OF = "2026-08-03"


def _instrument(conn, instrument_id, base, quote):
    conn.execute(
        "INSERT OR IGNORE INTO instruments VALUES (?,?,?,?,?,?,?,?)",
        (instrument_id, "FX", base, quote, 1, 0, f"{instrument_id} Curncy", "9999-12-31"),
    )


def _forward(conn, trade_id, instrument_id, quantity, rate, value_date,
             source="XLSX", account="BNPP-IPBFX-NMMF", trade_date=AS_OF, quote_amount=None):
    base, quote = instrument_id[:3], instrument_id[3:]
    conn.execute(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, source, instrument_id, "FX_FWD", trade_id, trade_date, quantity, rate,
         account, "cp", "HAHY7", "t", "d", ""),
    )
    if quote_amount is None:
        quote_amount = -quantity * rate
    conn.executemany(
        "INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (trade_id, 1, "FX_NEAR", base, quantity, trade_date, value_date, rate, 1),
            (trade_id, 2, "FX_NEAR", quote, quote_amount, trade_date, value_date, rate, 1),
        ],
    )


def test_swap_grouped_when_opposite_signed_and_different_value_dates():
    conn = schema.connect()
    _instrument(conn, "USDJPY", "USD", "JPY")
    _forward(conn, "201", "USDJPY", 1_000_000.0, 147.0, "2026-09-16")
    _forward(conn, "202", "USDJPY", -1_000_000.0, 147.0, "2026-09-26")
    conn.commit()

    packaged = swaps.package_swaps(conn)
    assert packaged == 2
    rows = conn.execute("SELECT trade_id, product, package_id FROM trades ORDER BY trade_id").fetchall()
    assert rows[0][1] == rows[1][1] == "FX_SWAP"
    assert rows[0][2] == rows[1][2] == "SWAP-201"
    assert conn.execute("SELECT COUNT(*) FROM swap_review").fetchone()[0] == 0


def test_swap_round_trip_same_value_date_not_grouped():
    """Opposite-signed rows with the SAME value date are an intraday round trip: they
    stay separate outrights, and are not flagged for review either."""
    conn = schema.connect()
    _instrument(conn, "USDJPY", "USD", "JPY")
    _forward(conn, "201", "USDJPY", 1_000_000.0, 147.0, "2026-09-16")
    _forward(conn, "202", "USDJPY", -1_000_000.0, 147.0, "2026-09-16")
    conn.commit()

    packaged = swaps.package_swaps(conn)
    assert packaged == 0
    products = {r[0] for r in conn.execute("SELECT product FROM trades")}
    assert products == {"FX_FWD"}
    assert conn.execute("SELECT COUNT(*) FROM swap_review").fetchone()[0] == 0


def test_swap_ambiguous_multiple_candidates_go_to_review():
    """A third row on the negative side with the same account/pair/trade date and a
    matching USD amount, at yet another value date, makes the positive leg's
    counterparty ambiguous: no auto-grouping, both plausible negatives go to review."""
    conn = schema.connect()
    _instrument(conn, "USDJPY", "USD", "JPY")
    _forward(conn, "201", "USDJPY", 1_000_000.0, 147.0, "2026-09-16")
    _forward(conn, "202", "USDJPY", -1_000_000.0, 147.0, "2026-09-26")
    _forward(conn, "203", "USDJPY", -1_000_000.0, 147.0, "2026-10-06")
    conn.commit()

    packaged = swaps.package_swaps(conn)
    assert packaged == 0
    products = {r[0] for r in conn.execute("SELECT product FROM trades")}
    assert products == {"FX_FWD"}
    reviewed = {r[0] for r in conn.execute("SELECT trade_id FROM swap_review")}
    assert reviewed == {"201", "202", "203"}


def test_swap_never_pairs_trades_from_different_sources():
    """2026-09-16 (trades_official double-count fix): two outrights on the same
    account/pair/trade_date, opposite sign, matching USD notional -- exactly what
    _matches() checks for -- must never be packaged together when they come from
    different sources, since a real swap's two legs are always booked on the same
    ticket (same source). Only same-source pairing is legitimate."""
    conn = schema.connect()
    _instrument(conn, "USDJPY", "USD", "JPY")
    _forward(conn, "bnp-1", "USDJPY", 1_000_000.0, 147.0, "2026-09-16", source="MANUAL")
    _forward(conn, "xlsx-1", "USDJPY", -1_000_000.0, 147.0, "2026-10-16", source="XLSX")
    conn.commit()

    packaged = swaps.package_swaps(conn)

    assert packaged == 0
    products = {r[0] for r in conn.execute("SELECT product FROM trades")}
    assert products == {"FX_FWD"}  # neither trade was fabricated into an FX_SWAP
    assert conn.execute("SELECT COUNT(*) FROM swap_review").fetchone()[0] == 0


def test_swap_packaging_is_idempotent():
    conn = schema.connect()
    _instrument(conn, "USDJPY", "USD", "JPY")
    _forward(conn, "201", "USDJPY", 1_000_000.0, 147.0, "2026-09-16")
    _forward(conn, "202", "USDJPY", -1_000_000.0, 147.0, "2026-09-26")
    conn.commit()

    swaps.package_swaps(conn)
    assert swaps.package_swaps(conn) == 0  # already packaged: nothing left to group


def test_swap_cross_with_no_usd_leg_is_grouped_on_base_amount():
    """A cross (EURSEK) has no USD leg, so the rule's |USD-leg| test cannot apply; the
    same 0.01 % tolerance is applied to |base quantity| instead. The real book had an
    EURSEK 5.9m buy / sell pair dealt 2026-08-24 for value 11-25 and 11-27 that stayed
    as two outrights before this fix."""
    conn = schema.connect()
    conn.execute("INSERT INTO instruments VALUES ('EURSEK','FX','EUR','SEK',1,0,'EURSEK Curncy','9999-12-31')")
    conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("938350940", "XLSX", "EURSEK", "FX_FWD", "938350940", "2026-08-24", 5900000.0, 11.10, "acc", "cp", "", "t", "d", ""),
        ("940235093", "XLSX", "EURSEK", "FX_FWD", "940235093", "2026-08-24", -5900000.0, 11.11, "acc", "cp", "", "t", "d", ""),
    ])
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("938350940", 1, "FX_NEAR", "EUR", 5900000.0, "2026-08-24", "2026-11-25", 11.10, 1),
        ("938350940", 2, "FX_NEAR", "SEK", -65490000.0, "2026-08-24", "2026-11-25", 11.10, 1),
        ("940235093", 1, "FX_NEAR", "EUR", -5900000.0, "2026-08-24", "2026-11-27", 11.11, 1),
        ("940235093", 2, "FX_NEAR", "SEK", 65549000.0, "2026-08-24", "2026-11-27", 11.11, 1),
    ])
    conn.commit()
    assert swaps.package_swaps(conn) == 2
    rows = conn.execute("SELECT product, package_id FROM trades ORDER BY trade_id").fetchall()
    assert rows == [("FX_SWAP", "SWAP-938350940"), ("FX_SWAP", "SWAP-938350940")]
