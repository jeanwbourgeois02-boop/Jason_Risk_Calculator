"""FX swap packaging: CLAUDE.md "Data contract -> package_id rule (FX swaps)".

Two FORWARD trades form one FX_SWAP package when all hold: same account, same pair,
same trade date, opposite-signed quantities, equal |USD-leg amount| within 0.01 %,
different value dates. package_id = 'SWAP-' || min(trade_id); the near leg is the one
with the earlier value date. Opposite-signed rows with the SAME value date are intraday
round trips and stay separate outrights (no package, no review). Groups where a trade
has more than one viable counterparty on the other side are never auto-grouped: they are
recorded in `swap_review` instead, one row per ambiguous trade_id.

`package_swaps(conn)` is idempotent: it only looks at trades still at product='FX_FWD'
with package_id == trade_id (i.e. not yet packaged), so re-running after a package has
been assigned does nothing to it. It should run at the end of every BNP upload.
"""
from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional, Tuple

from data.ingest import schema

REL_TOL = 1e-4  # 0.01 %


def _value_date(conn: sqlite3.Connection, trade_id: str) -> Optional[str]:
    """The single settle_date shared by a forward's two legs, or None if ambiguous."""
    rows = conn.execute(
        "SELECT DISTINCT settle_date FROM trade_legs WHERE trade_id = ?", (trade_id,)
    ).fetchall()
    return rows[0][0] if len(rows) == 1 else None


def _usd_leg_amount(conn: sqlite3.Connection, trade_id: str) -> Optional[float]:
    """abs(USD leg amount) for a forward, or None if the trade has no USD leg (a cross)."""
    row = conn.execute(
        "SELECT amount FROM trade_legs WHERE trade_id = ? AND ccy = 'USD'", (trade_id,)
    ).fetchone()
    return abs(row[0]) if row else None


def _matches(candidate: dict, other: dict) -> bool:
    return (candidate["value_date"] != other["value_date"]
            and other["usd"] > 0
            and abs(candidate["usd"] - other["usd"]) <= other["usd"] * REL_TOL)


def package_swaps(conn: sqlite3.Connection) -> int:
    """Group unpackaged FORWARD trades into FX_SWAP packages. Returns the count of trades
    (always even) newly packaged. Ambiguous candidates are written to `swap_review`."""
    schema.create_schema(conn)
    candidates = conn.execute(
        "SELECT trade_id, account, instrument_id, trade_date, quantity FROM trades "
        "WHERE product = 'FX_FWD' AND package_id = trade_id"
    ).fetchall()

    groups: Dict[Tuple[str, str, str], List[dict]] = {}
    for trade_id, account, instrument_id, trade_date, quantity in candidates:
        groups.setdefault((account, instrument_id, trade_date), []).append(
            {"trade_id": trade_id, "quantity": quantity})

    packaged = 0
    review_rows: List[Tuple[str, str, str]] = []

    for key, trades in groups.items():
        if len(trades) < 2:
            continue
        for t in trades:
            t["value_date"] = _value_date(conn, t["trade_id"])
            t["usd"] = _usd_leg_amount(conn, t["trade_id"])
        eligible = [t for t in trades if t["value_date"] is not None and t["usd"] is not None]
        pos = [t for t in eligible if t["quantity"] > 0]
        neg = [t for t in eligible if t["quantity"] < 0]
        candidate_group = f"{key[0]}|{key[1]}|{key[2]}"

        for p in pos:
            matches = [n for n in neg if _matches(p, n)]
            if not matches:
                continue  # no counterparty at all: an ordinary outright, not ambiguous
            if len(matches) > 1:
                review_rows.append((candidate_group, p["trade_id"], "more than one swap candidate on the other side"))
                for m in matches:
                    review_rows.append((candidate_group, m["trade_id"], "more than one swap candidate on the other side"))
                continue
            n = matches[0]
            reverse = [pp for pp in pos if _matches(n, pp)]
            if len(reverse) != 1 or reverse[0]["trade_id"] != p["trade_id"]:
                review_rows.append((candidate_group, p["trade_id"], "more than one swap candidate on the other side"))
                review_rows.append((candidate_group, n["trade_id"], "more than one swap candidate on the other side"))
                continue
            package_id = "SWAP-" + min(p["trade_id"], n["trade_id"])
            conn.execute("UPDATE trades SET product = 'FX_SWAP', package_id = ? WHERE trade_id = ?",
                         (package_id, p["trade_id"]))
            conn.execute("UPDATE trades SET product = 'FX_SWAP', package_id = ? WHERE trade_id = ?",
                         (package_id, n["trade_id"]))
            packaged += 2

    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO swap_review (candidate_group, trade_id, reason) VALUES (?,?,?)",
            review_rows)
    conn.commit()
    return packaged
