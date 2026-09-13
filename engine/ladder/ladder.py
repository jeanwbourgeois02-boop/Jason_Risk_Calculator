"""Cash ladder and delta-per-currency queries. See CLAUDE.md "Six tabs as views" (Cash
ladder row) and "Aggregate delta per currency" SQL.

All queries are parameterised (no f-string interpolation of dates) and read only from
`trade_legs`, `positions` and `marks_official`. Dependency-light: sqlite3 + pandas.
"""
from __future__ import annotations

import sqlite3

import pandas as pd

# ------------------------------------------------------------------------- cash ladder
_LEG_SQL = """
SELECT ccy, settle_date, SUM(amount) AS amount
FROM trade_legs
WHERE settles_cash = 1 AND settle_date >= :as_of
GROUP BY ccy, settle_date
"""

_CASH_POSITION_SQL = """
SELECT instrument_id, settle_date, SUM(quantity) AS quantity
FROM positions
WHERE instrument_id LIKE 'CASH-%' AND as_of_date = :as_of AND source = :source
GROUP BY instrument_id, settle_date
"""


def cash_ladder(conn: sqlite3.Connection, as_of_date: str, source: str = "BNP") -> pd.DataFrame:
    """One row per (ccy, settle_date, kind).

    LEG rows: SUM(trade_legs.amount) grouped by (ccy, settle_date) where
    settles_cash = 1 AND settle_date >= as_of_date (CLAUDE.md "Six tabs as views" ->
    Cash ladder). CASH rows: `positions` rows with instrument_id LIKE 'CASH-%',
    as_of_date = :as_of and source = :source (default 'BNP'), SUMmed and GROUPed BY
    (instrument_id, settle_date) so the real file's multiple per-account CASH-<CCY> rows
    (e.g. 6 separate CASH-USD rows, one per account, on 2026-08-17) collapse to one row
    per (ccy, settle_date). The `source` filter defaults to 'BNP' so CALC rows the P&L
    engine writes to `positions` later (recomputed net positions) are never
    double-counted alongside the raw PB snapshot; pass source='CALC' explicitly to see
    those instead. ccy = the instrument_id's '<CCY>' suffix, settle_date = as_of_date,
    amount = quantity. CASH and LEG rows for the same (ccy, settle_date) are kept as
    separate rows (never merged) via the `kind` column, since a CASH position row and a
    forward's cash-settling leg are conceptually different things even when they land on
    the same date. Columns: ccy, settle_date, kind, amount. Sorted by ccy, settle_date.
    """
    legs = pd.read_sql_query(_LEG_SQL, conn, params={"as_of": as_of_date})
    legs["kind"] = "LEG"

    pos = pd.read_sql_query(
        _CASH_POSITION_SQL, conn, params={"as_of": as_of_date, "source": source}
    )
    pos["ccy"] = pos["instrument_id"].str.replace("^CASH-", "", regex=True)
    pos["amount"] = pos["quantity"]
    pos["kind"] = "CASH"
    pos = pos[["ccy", "settle_date", "kind", "amount"]]

    out = pd.concat([legs[["ccy", "settle_date", "kind", "amount"]], pos], ignore_index=True)
    out = out.sort_values(["ccy", "settle_date", "kind"]).reset_index(drop=True)
    return out


# ------------------------------------------------------------------------- delta per ccy
# Copied verbatim from CLAUDE.md "Six tabs as views" -> "Aggregate delta per currency"
# (the CLAUDE.md SQL already names the parameter :as_of; kept identical here).
_DELTA_SQL = """
WITH d AS (
  SELECT l.ccy, l.amount AS delta
  FROM trade_legs l JOIN trades t USING (trade_id)
  WHERE t.product IN ('FX_SPOT','FX_FWD','FX_SWAP','FUTURE') AND l.settle_date > :as_of
  UNION ALL
  SELECT i.base_ccy, t.quantity * m.value
  FROM trades t JOIN instruments i USING (instrument_id)
  JOIN marks_official m ON m.instrument_id = t.instrument_id AND m.mark_type = 'DELTA' AND m.as_of_date = :as_of
  WHERE t.product = 'FX_OPTION'
  UNION ALL
  SELECT i.quote_ccy, -t.quantity * m.value * s.value
  FROM trades t JOIN instruments i USING (instrument_id)
  JOIN marks_official m ON m.instrument_id = t.instrument_id AND m.mark_type = 'DELTA' AND m.as_of_date = :as_of
  JOIN marks_official s ON s.instrument_id = t.instrument_id AND s.mark_type = 'SPOT'  AND s.as_of_date = :as_of
  WHERE t.product = 'FX_OPTION'
)
SELECT ccy, SUM(delta) AS delta FROM d GROUP BY ccy
"""


def delta_per_ccy(conn: sqlite3.Connection, as_of_date: str) -> pd.DataFrame:
    """Aggregate delta per currency (forwards/futures legs + FX option deltas), exactly the
    CLAUDE.md "Aggregate delta per currency" SQL, reading only from marks_official.

    Adds `delta_usd` = delta x official SPOT (quote->USD) for that ccy on as_of_date; 1.0
    for USD; NaN when no SPOT mark exists for that ccy (never estimated, never taken from
    positions.fx_to_usd).
    """
    delta = pd.read_sql_query(_DELTA_SQL, conn, params={"as_of": as_of_date})
    spot = spot_table(conn, as_of_date)
    spot_map = dict(zip(spot["ccy"], spot["spot"]))

    def _rate(ccy: str) -> float:
        if ccy == "USD":
            return 1.0
        return spot_map.get(ccy, float("nan"))

    delta["delta_usd"] = delta.apply(lambda r: r["delta"] * _rate(r["ccy"]), axis=1)
    return delta


# ------------------------------------------------------------------------- spot table
_SPOT_SQL = """
SELECT instrument_id, value
FROM marks_official
WHERE mark_type = 'SPOT' AND as_of_date = :as_of AND settle_date = :as_of
"""


def spot_table(conn: sqlite3.Connection, as_of_date: str) -> pd.DataFrame:
    """ccy -> USD spot rate, derived from official SPOT marks (marks_official, mark_type =
    'SPOT', settle_date = as_of_date per the contract: "settle_date = as_of_date for
    SPOT") as of as_of_date.

    Instrument ids are 6-letter pairs (e.g. 'USDJPY', 'AUDUSD', 'XAUUSD'). For a pair
    whose base is USD ('USDJPY'), the non-USD ccy is the quote and ccy->USD = 1/value.
    For a pair whose quote is USD ('AUDUSD'), the non-USD ccy is the base and
    ccy->USD = value directly. Pairs with neither side USD (crosses, e.g. 'EURSEK') are
    ignored: there is no single spot mark that converts them to USD. A zero or negative
    SPOT value never produces an estimated rate: that ccy's row is dropped from the
    intermediate list and it falls back to NaN downstream (never divide by zero, never
    invert a non-positive rate). If both a USDXXX and an XXXUSD instrument give a rate
    for the same ccy (shouldn't normally happen but is not excluded by the schema), the
    XXXUSD (direct quote) row is preferred, deterministically: rows are sorted by
    (ccy, direct desc) with a stable sort before dropping duplicates, never relying on
    unstable/quicksort ordering.
    """
    marks = pd.read_sql_query(_SPOT_SQL, conn, params={"as_of": as_of_date})
    rows = []
    for _, r in marks.iterrows():
        pair = r["instrument_id"]
        if len(pair) != 6 or not pair.isalpha():
            continue
        base, quote = pair[:3], pair[3:]
        value = float(r["value"])
        if base == "USD" and quote != "USD":
            if value > 0:
                rows.append((quote, 1.0 / value, False))  # indirect
        elif quote == "USD" and base != "USD":
            if value > 0:
                rows.append((base, value, True))  # direct, preferred
        # neither side USD: ignore (cross)

    if not rows:
        return pd.DataFrame({"ccy": [], "spot": []})

    df = pd.DataFrame(rows, columns=["ccy", "spot", "direct"])
    # prefer direct (XXXUSD) quotes when both exist for the same ccy; explicit,
    # deterministic sort key (stable mergesort) rather than relying on quicksort order.
    df = df.sort_values(
        ["ccy", "direct"], ascending=[True, False], kind="mergesort"
    ).drop_duplicates("ccy", keep="first")
    df = df[["ccy", "spot"]].sort_values("ccy", kind="mergesort").reset_index(drop=True)
    return df


# ------------------------------------------------------------------------- USD conversion
def convert_to_usd(ladder: pd.DataFrame, spot: pd.DataFrame) -> pd.DataFrame:
    """Add `amount_usd` = amount x spot to a ladder-shaped DataFrame (must have `ccy` and
    `amount` columns). USD converts at 1.0 even if absent from `spot`. Any ccy missing
    from `spot` gets amount_usd = NaN (never estimated). Pure function: does not touch
    the DB.
    """
    spot_map = dict(zip(spot["ccy"], spot["spot"]))

    def _rate(ccy: str) -> float:
        if ccy == "USD":
            return 1.0
        return spot_map.get(ccy, float("nan"))

    out = ladder.copy()
    out["amount_usd"] = out.apply(lambda r: r["amount"] * _rate(r["ccy"]), axis=1)
    return out
