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
SELECT l.ccy, l.settle_date, SUM(l.amount) AS amount
FROM trade_legs l JOIN trades_official t USING (trade_id)
WHERE l.settles_cash = 1 AND l.settle_date >= :as_of AND t.trade_date <= :as_of
GROUP BY l.ccy, l.settle_date
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
# Adapted from CLAUDE.md "Six tabs as views" -> "Aggregate delta per currency" (the
# CLAUDE.md SQL already names the parameter :as_of; kept identical here) except every
# `trades` reference reads `trades_official` instead (user decision 2026-09-16: BNP is
# no longer an authoritative trade source, only the blotter and MANUAL are -- see
# data/ingest/schema.py's trades_official view and docs/open-questions.md item 55) and
# the FX_OPTION quote-ccy branch's SPOT join is a LEFT JOIN (docs/open-questions.md item
# 25, fixed 2026-09-17): CLAUDE.md's own SQL joins `s.instrument_id = t.instrument_id`,
# which can never match, because an option's instrument_id is its own contract id (e.g.
# 'USDJPY111926P-1'), not the 6-letter pair the SPOT mark is keyed on ('USDJPY'). Joining
# on the option's own id would make the LEFT JOIN permanently miss (spot always NULL) for
# every real option, silently zeroing every option's quote-ccy delta -- worse than the
# inner-join bug this item fixes. Instead this joins on the pair derived from
# instruments.base_ccy || instruments.quote_ccy (CLAUDE.md's own suggested alternative),
# which is well-defined for every FX_OPTION instrument regardless of its own instrument_id
# shape. CLAUDE.md's literal SQL should be corrected the same way (flagged to housekeeper).
_DELTA_SQL = """
WITH d AS (
  SELECT l.ccy, l.amount AS delta
  FROM trade_legs l JOIN trades_official t USING (trade_id)
  WHERE t.product IN ('FX_SPOT','FX_FWD','FX_SWAP','FUTURE') AND l.settle_date > :as_of
  UNION ALL
  SELECT i.base_ccy, t.quantity * m.value
  FROM trades_official t JOIN instruments i USING (instrument_id)
  JOIN marks_official m ON m.instrument_id = t.instrument_id AND m.mark_type = 'DELTA' AND m.as_of_date = :as_of
  WHERE t.product = 'FX_OPTION'
  UNION ALL
  SELECT i.quote_ccy, -t.quantity * m.value * s.value
  FROM trades_official t JOIN instruments i USING (instrument_id)
  JOIN marks_official m ON m.instrument_id = t.instrument_id AND m.mark_type = 'DELTA' AND m.as_of_date = :as_of
  LEFT JOIN marks_official s ON s.instrument_id = i.base_ccy || i.quote_ccy AND s.mark_type = 'SPOT' AND s.as_of_date = :as_of
  WHERE t.product = 'FX_OPTION'
)
SELECT ccy, SUM(delta) AS delta FROM d GROUP BY ccy
"""

# Diagnostic, pre-aggregation check for the same FX_OPTION quote-ccy branch: SQL SUM
# ignores individual NULL values within a GROUP BY group, so a missing SPOT mark on one
# option would be silently dropped out of that ccy's total whenever the group also
# contains other, unrelated non-NULL contributions (e.g. a forward leg also settling in
# that quote ccy) -- the exact "drop the leg silently" failure CLAUDE.md forbids. This
# query finds any FX_OPTION with an official DELTA mark but no official SPOT mark for its
# pair, at the row level, before that silent aggregation can happen.
_OPTION_MISSING_SPOT_SQL = """
SELECT DISTINCT t.instrument_id
FROM trades_official t JOIN instruments i USING (instrument_id)
JOIN marks_official m ON m.instrument_id = t.instrument_id AND m.mark_type = 'DELTA' AND m.as_of_date = :as_of
LEFT JOIN marks_official s ON s.instrument_id = i.base_ccy || i.quote_ccy AND s.mark_type = 'SPOT' AND s.as_of_date = :as_of
WHERE t.product = 'FX_OPTION' AND s.value IS NULL
"""


def delta_per_ccy(conn: sqlite3.Connection, as_of_date: str) -> pd.DataFrame:
    """Aggregate delta per currency (forwards/futures legs + FX option deltas), exactly the
    CLAUDE.md "Aggregate delta per currency" SQL, reading only from marks_official.

    Raises ValueError (naming the offending instrument_id(s) and as_of_date) if any
    FX_OPTION with an official DELTA mark is missing an official SPOT mark for its pair:
    per CLAUDE.md, "the engine must raise if any resulting delta is NULL, never drop the
    leg silently". Checked at the row level (see _OPTION_MISSING_SPOT_SQL) rather than by
    inspecting the aggregated output, because SQL SUM can silently absorb a single NULL
    contribution into an otherwise non-NULL ccy group.

    Adds `delta_usd` = delta x official SPOT (quote->USD) for that ccy on as_of_date; 1.0
    for USD; NaN when no SPOT mark exists for that ccy (never estimated, never taken from
    positions.fx_to_usd).
    """
    missing = pd.read_sql_query(_OPTION_MISSING_SPOT_SQL, conn, params={"as_of": as_of_date})
    if not missing.empty:
        ids = ", ".join(sorted(missing["instrument_id"]))
        raise ValueError(
            f"delta_per_ccy({as_of_date}): missing official SPOT mark for FX_OPTION "
            f"quote-ccy delta, instrument(s): {ids}"
        )

    delta = pd.read_sql_query(_DELTA_SQL, conn, params={"as_of": as_of_date})
    if delta["delta"].isna().any():
        bad = ", ".join(sorted(delta.loc[delta["delta"].isna(), "ccy"]))
        raise ValueError(f"delta_per_ccy({as_of_date}): NULL delta for ccy(s): {bad}")

    spot = spot_table(conn, as_of_date)
    spot_map = dict(zip(spot["ccy"], spot["spot"]))

    def _rate(ccy: str) -> float:
        if ccy == "USD":
            return 1.0
        return spot_map.get(ccy, float("nan"))

    delta["delta_usd"] = delta.apply(lambda r: r["delta"] * _rate(r["ccy"]), axis=1)
    return delta


# ------------------------------------------------------------------------- spot table
_SPOT_OFFICIAL_SQL = """
SELECT instrument_id, value
FROM marks_official
WHERE mark_type = 'SPOT' AND as_of_date = :as_of AND settle_date = :as_of
"""

_SPOT_SOURCE_SQL = """
SELECT instrument_id, value
FROM marks
WHERE mark_type = 'SPOT' AND as_of_date = :as_of AND settle_date = :as_of AND source = :source
"""


def spot_table(conn: sqlite3.Connection, as_of_date: str, source: "str | None" = None) -> pd.DataFrame:
    """ccy -> USD spot rate, derived from official SPOT marks (marks_official, mark_type =
    'SPOT', settle_date = as_of_date per the contract: "settle_date = as_of_date for
    SPOT") as of as_of_date.

    ``source``: None (default, unchanged behaviour) reads from `marks_official`. An
    explicit source (e.g. 'BNP_BVAL') reads the raw `marks` table filtered to that one
    source instead -- reconciliation only, mirroring engine/pnl/pnl.py's `_marks_df`
    pattern, since BNP_BVAL is never official per CLAUDE.md "Official marks".

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
    if source is None:
        marks = pd.read_sql_query(_SPOT_OFFICIAL_SQL, conn, params={"as_of": as_of_date})
    else:
        marks = pd.read_sql_query(
            _SPOT_SOURCE_SQL, conn, params={"as_of": as_of_date, "source": source}
        )
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
