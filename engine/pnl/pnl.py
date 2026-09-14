"""Per-trade life-to-date (LTD) P&L for FX forwards. See CLAUDE.md "P&L conventions".

Only FX_FWD is implemented here (futures / IRS / FX_OPTION LTD formulas are documented
in CLAUDE.md but out of scope until those products are exercised end to end).

Sign and formula, per CLAUDE.md "P&L conventions -> Per-trade LTD P&L (USD)":
    Q = trades.quantity (signed base-ccy amount), f = trades.price (fill rate)
    m = FWD_OUTRIGHT mark for the trade's OWN settle_date (never one shared T+n date)
    S = quote_ccy -> USD spot on as_of_date (never the forward outright)
    PnL_quote = Q x (m - f)
    PnL_USD   = PnL_quote x S   (S = 1.0 when quote_ccy is USD)

"Must not replicate" items this module is careful to avoid (CLAUDE.md list):
  - no division by the outright mark or the fill (the futures-style shortcut) is used here.
  - USD conversion always at spot, never at the forward outright.
  - each leg marked at its own settle_date, never one shared WORKDAY(today, 5) date.
A grep test (tests/test_pnl.py) enforces that no line in engine/pnl/*.py divides by a
variable literally named `mark`, `m` or `spot`; the one division this module performs
(pair rate -> USD) uses the name `pair_rate` for that reason.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

import pandas as pd

OUTPUT_COLUMNS = [
    "trade_id", "instrument_id", "settle_date", "quantity", "fill", "mark", "spot",
    "pnl_quote", "pnl_usd", "source",
]

_TRADE_SQL = """
SELECT t.trade_id, t.instrument_id, l.settle_date, t.quantity, t.price AS fill,
       i.quote_ccy
FROM trades t
JOIN instruments i ON i.instrument_id = t.instrument_id
JOIN trade_legs l ON l.trade_id = t.trade_id AND l.leg_no = 1
WHERE t.product = 'FX_FWD' AND l.settle_date > :as_of AND t.trade_date <= :as_of
"""

_MARKS_OFFICIAL_SQL = """
SELECT instrument_id, settle_date, value
FROM marks_official
WHERE mark_type = :mark_type AND as_of_date = :as_of
"""

_MARKS_SOURCE_SQL = """
SELECT instrument_id, settle_date, value
FROM marks
WHERE mark_type = :mark_type AND as_of_date = :as_of AND source = :source
"""


def _marks_df(conn: sqlite3.Connection, as_of_date: str, mark_type: str,
              source: Optional[str]) -> pd.DataFrame:
    """Marks of one mark_type on as_of_date, from marks_official (source=None) or from
    the raw marks table filtered to one explicit source (reconciliation only)."""
    if source is None:
        return pd.read_sql_query(_MARKS_OFFICIAL_SQL, conn,
                                  params={"mark_type": mark_type, "as_of": as_of_date})
    return pd.read_sql_query(_MARKS_SOURCE_SQL, conn,
                              params={"mark_type": mark_type, "as_of": as_of_date, "source": source})


def _pair_spot_lookup(conn: sqlite3.Connection, as_of_date: str, source: Optional[str]) -> dict:
    """instrument_id (pair) -> SPOT value (quote units per 1 base unit), restricted to
    settle_date = as_of_date per the contract ("settle_date = as_of_date for SPOT")."""
    df = _marks_df(conn, as_of_date, "SPOT", source)
    df = df[df["settle_date"] == as_of_date]
    return dict(zip(df["instrument_id"], df["value"]))


def _usd_per_quote(quote_ccy: str, pair_spot: dict) -> float:
    """quote_ccy -> USD conversion factor S.

    quote_ccy == 'USD': S = 1.0 (trivial; e.g. AUDUSD).
    Otherwise: look up the SPOT of the 'USD<quote_ccy>' pair (value = quote_ccy units
    per 1 USD, e.g. USDJPY ~= 147); S = 1 / that pair_rate. This covers both USDXXX
    forwards directly (whose own instrument_id already is 'USD<quote_ccy>') and crosses
    (e.g. EURSEK: looks up 'USDSEK', a different instrument's mark, not EURSEK's own
    SPOT). Missing pair or non-positive rate -> NaN, never estimated.
    """
    if quote_ccy == "USD":
        return 1.0
    pair_rate = pair_spot.get(f"USD{quote_ccy}")
    if pair_rate is None or pair_rate <= 0:
        return float("nan")
    return 1.0 / pair_rate


def ltd_per_trade(conn: sqlite3.Connection, as_of_date: str,
                   source: Optional[str] = None, strict: bool = True) -> pd.DataFrame:
    """LTD USD P&L per open FX_FWD trade (settle_date > as_of_date) as of as_of_date.

    ``source``: None (default) reads marks from marks_official (the correct P&L path);
    an explicit source (e.g. 'BNP_BVAL') reads only that source from the raw marks table
    -- reconciliation only, since BNP_BVAL is never official. The output `source` column
    records 'OFFICIAL' when source is None (marks_official may blend several official
    sources across mark_types) or the explicit source string otherwise.

    Missing FWD_OUTRIGHT mark or missing quote->USD spot -> NaN in the affected output
    column(s), never estimated or substituted. With strict=True (default) any resulting
    NaN pnl_usd raises ValueError naming the offending trade_id(s); strict=False returns
    the NaNs.
    """
    trades = pd.read_sql_query(_TRADE_SQL, conn, params={"as_of": as_of_date})
    if trades.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    outright = _marks_df(conn, as_of_date, "FWD_OUTRIGHT", source)
    outright = outright.rename(columns={"value": "mark"})
    trades = trades.merge(outright, on=["instrument_id", "settle_date"], how="left")

    pair_spot = _pair_spot_lookup(conn, as_of_date, source)
    trades["spot"] = trades["quote_ccy"].apply(lambda q: _usd_per_quote(q, pair_spot))

    trades["pnl_quote"] = trades["quantity"] * (trades["mark"] - trades["fill"])
    trades["pnl_usd"] = trades["pnl_quote"] * trades["spot"]
    trades["source"] = "OFFICIAL" if source is None else source

    out = trades[OUTPUT_COLUMNS].reset_index(drop=True)

    if strict:
        bad = out.loc[out["pnl_usd"].isna(), "trade_id"].tolist()
        if bad:
            raise ValueError(
                f"ltd_per_trade: missing mark or spot -> NaN pnl_usd for trade_id(s): {sorted(bad)}")

    return out
