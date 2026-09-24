"""Cash ladder and delta-per-currency queries. See CLAUDE.md "Six tabs as views" (Cash
ladder row) and "Aggregate delta per currency" SQL.

All queries are parameterised (no f-string interpolation of dates) and read only from
`trade_legs` and `marks_official`. Dependency-light: sqlite3 + pandas.

2026-09-17 ("no bnp fall back" -- user decision, `docs/bnp-excel-removal.md`): the
ladder's CASH-balance column (a `positions` row with `instrument_id LIKE 'CASH-%'`)
was fed exclusively by the BNP daily snapshot; BNP is no longer ingested by the app at
all, so that column was always empty (inert dead weight), never a real cash balance.
Dropped outright rather than left inert -- see `cash_ladder`'s docstring. The cash
ladder is now, and only ever needs to be, the pure `trade_legs` cashflow-timing view
CLAUDE.md's "Six tabs as views" describes: "cashflow timing and delta exposure only;
no cash-balance rows".
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from engine.ladder.exposure import COMMODITY_CCYS

# ------------------------------------------------------------------------- cash ladder
_LEG_SQL = """
SELECT l.ccy, l.settle_date, SUM(l.amount) AS amount
FROM trade_legs l JOIN trades_official t USING (trade_id)
WHERE l.settles_cash = 1 AND l.settle_date >= :as_of AND t.trade_date <= :as_of
GROUP BY l.ccy, l.settle_date
"""


def cash_ladder(conn: sqlite3.Connection, as_of_date: str) -> pd.DataFrame:
    """One row per (ccy, settle_date): SUM(trade_legs.amount) where settles_cash = 1
    AND settle_date >= as_of_date AND trade_date <= as_of_date (CLAUDE.md "Six tabs as
    views" -> Cash ladder). Columns: ccy, settle_date, kind, amount. Sorted by ccy,
    settle_date.

    No CASH/`positions` row any more (removed 2026-09-17, "no bnp fall back" -- see
    module docstring): the only thing that ever wrote a `positions` row with
    `instrument_id LIKE 'CASH-%'` was the retired BNP daily snapshot, so that branch
    always returned zero rows once BNP stopped being ingested. The `source` parameter
    (which used to pick 'BNP' vs 'CALC' `positions` rows) is gone entirely -- this is
    now purely the leg-level cashflow-timing view. `kind` is kept as a column, always
    'LEG', only because `tests/test_trades_official.py` (data-ingest's lane, off-limits
    here) still filters on it; safe for a future pass to drop once that test is updated
    -- flagged, not done here to avoid breaking a lane this agent cannot edit.

    LME forwards (LME_FWD, Phase 5): the USD leg (settles_cash 1) is cash on its prompt
    date like any FX leg; the metal leg (ccy = the root id, 'LME:CA') has settles_cash 0
    and is never a row here -- the metal is a Curve-tab position, not a currency.
    """
    legs = pd.read_sql_query(_LEG_SQL, conn, params={"as_of": as_of_date})
    legs["kind"] = "LEG"
    return legs[["ccy", "settle_date", "kind", "amount"]].sort_values(
        ["ccy", "settle_date"]
    ).reset_index(drop=True)


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
#
# LME forwards (LME_FWD, Phase 5) are not in the first branch's product list, so an LME
# ticket contributes nothing here: its metal leg is never a currency, and its USD leg is
# left out with it (the ladder's own records carry that leg in the USD row, which adds
# nothing to the non-USD net). The FUTURE in that list counts a future's NOTIONAL leg
# (contracts x multiplier x fill) as delta in its quote currency; both are CLAUDE.md's
# contract SQL and change only on the user's yes. No screen calls delta_per_ccy.
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


# ------------------------------------------------------------------- per-pair Position
# CLAUDE.md "Aggregate delta per currency": "Per-pair delta (the sheet's "Position") is
# the same union grouped by t.instrument_id in USD-notional terms." This implements
# that, extended per the user's 2026-09-17 "dollar convention" instruction ("for aud,
# eur and gbp - convention adjusted for dollar convention - it needs to be done"):
# AUDUSD/EURUSD/GBPUSD/NZDUSD/XAUUSD (quote_ccy == 'USD') are quoted the OPPOSITE way
# round from USDJPY-style pairs (base_ccy == 'USD'), so a single "USD notional" number
# is ambiguous unless the sign convention is named. This function returns BOTH:
#
#   - `notional_base`: CLAUDE.md's own "Display notional" (the xlsx convention) -- USD
#     notional signed by the BASE currency's own direction (+ = bought base), i.e.
#     sign(base leg) x |USD leg| exactly as CLAUDE.md states. Equal to the USD leg
#     itself when base_ccy == 'USD' (no sign flip: the base leg IS the USD leg); the
#     mirror-image sign of the USD leg when quote_ccy == 'USD' (long AUD, i.e. base
#     leg > 0, is short USD, i.e. USD/quote leg < 0 -- notional_base takes the base
#     leg's sign, +, over the USD leg's own sign, -).
#   - `notional_usd`: the "dollar convention" -- USD notional signed by the USD
#     direction itself (+ = long USD). This is simply the trade's own USD leg amount,
#     unchanged (trade_legs.amount is already "+ = receive/long" in that leg's own
#     currency, so the USD leg's amount IS already USD-direction-signed with no
#     transformation needed). Identical to `notional_base` when base_ccy == 'USD';
#     sign-flipped relative to `notional_base` when quote_ccy == 'USD' -- this is the
#     "dollar convention" adjustment the user asked for -- EXCEPT for a commodity pair
#     (XAUUSD etc., `commodity` below): gold is a metal position, not a dollar position,
#     and the 2026-09-17 instruction named AUD, EUR and GBP only. Applying the flip to
#     gold showed a long gold book as a negative number ("gold the sign is the wrong
#     one", 2026-09-18), so for a commodity pair `notional_usd` keeps the metal's own
#     sign (+ = long the metal), i.e. equals `notional_base`.
#   - `spot`: the pair's own official SPOT mark exactly as quoted (never inverted), e.g.
#     AUDUSD 0.66, USDJPY 150, EURSEK ~11.05 -- read directly off marks_official keyed
#     on the pair's own instrument_id. This is independent of, and never derived from,
#     the ccy->USD conversion `spot_table` performs for crosses below.
#   - `move_1pct_usd` = `notional_base` x 0.01, NOT `notional_usd` x 0.01: a 1% RISE in
#     the pair's own quoted price benefits a position with positive `notional_base` by
#     construction for every pair (long AUDUSD gains when AUDUSD rises; long USDJPY
#     gains when USDJPY rises) -- using the USD-direction-signed `notional_usd` here
#     would invert the sign of the P&L for every quote_ccy == 'USD' pair.
#   - `cross` (bool): neither leg is USD (e.g. EURSEK). There is then no USD leg and no
#     distinct "USD direction" to speak of, so both `notional_base` and `notional_usd`
#     fall back to the SAME number -- the base currency's own leg converted at ITS OWN
#     ccy->USD spot (e.g. EUR's own EURUSD rate from `spot_table`, never a EURSEK rate,
#     which converts EUR/SEK to each other, not to USD). NaN if that spot is missing
#     (never estimated). This single per-pair number intentionally does NOT capture the
#     quote currency's own exposure (e.g. SEK) -- that remains fully visible, as its own
#     independent, correctly-converted row, only in the per-CURRENCY delta table
#     (`delta_per_ccy` above / `cash_ladder`); a EURSEK forward must never be reduced to
#     one currency line there (user's separate 2026-09-17 complaint, "the eursek has not
#     been split well") -- this per-pair table is an addition, not a replacement.
#
# Scope: FX_SPOT / FX_FWD / FX_SWAP only (matches the xlsx workbook's "All FX trades"
# sheet). FUTURE is excluded (see futures_delta.py, its own module, its own Position
# concept -- contracts x multiplier x price, not a currency pair). FX_OPTION is excluded
# (CLAUDE.md "Options tab placement": options live in the Blotter's own grouped
# trade summary, not this Position table). Delta convention throughout: settle_date >
# as_of (mirrors `_DELTA_SQL` / `futures_delta.py`), not the cash ladder grid's `>=`.
_PAIR_LEG_SQL = """
SELECT t.instrument_id, i.base_ccy, i.quote_ccy, l.ccy, SUM(l.amount) AS amount
FROM trade_legs l JOIN trades_official t USING (trade_id) JOIN instruments i USING (instrument_id)
WHERE t.product IN ('FX_SPOT','FX_FWD','FX_SWAP') AND l.settle_date > :as_of
GROUP BY t.instrument_id, i.base_ccy, i.quote_ccy, l.ccy
"""

_PAIR_SPOT_SQL = """
SELECT instrument_id, value
FROM marks_official
WHERE mark_type = 'SPOT' AND as_of_date = :as_of AND settle_date = :as_of
"""

PAIR_COLUMNS = ["instrument_id", "base_ccy", "quote_ccy", "cross", "commodity", "spot",
                "notional_base", "notional_usd", "move_1pct_usd"]


def _signed_magnitude(magnitude: float, sign_of: float) -> float:
    """abs(magnitude) with the sign of `sign_of`; 0.0 if `sign_of` is exactly 0 (a
    fully-netted base leg carries no directional sign to borrow)."""
    if sign_of > 0:
        return abs(magnitude)
    if sign_of < 0:
        return -abs(magnitude)
    return 0.0


def per_pair_delta(conn: sqlite3.Connection, as_of_date: str) -> pd.DataFrame:
    """One row per open FX pair (instrument_id): `PAIR_COLUMNS` above. Open = any
    FX_SPOT/FX_FWD/FX_SWAP trade with a leg settling after as_of_date (delta
    convention). Sorted by |notional_base| descending. Empty DataFrame (right columns,
    zero rows) when there are no open FX pairs.
    """
    legs = pd.read_sql_query(_PAIR_LEG_SQL, conn, params={"as_of": as_of_date})
    if legs.empty:
        return pd.DataFrame(columns=PAIR_COLUMNS)

    pair_spot_raw = pd.read_sql_query(_PAIR_SPOT_SQL, conn, params={"as_of": as_of_date})
    pair_spot = dict(zip(pair_spot_raw["instrument_id"], pair_spot_raw["value"]))

    ccy_spot = spot_table(conn, as_of_date)
    ccy_spot_map = dict(zip(ccy_spot["ccy"], ccy_spot["spot"]))

    rows = []
    for (instrument_id, base_ccy, quote_ccy), grp in legs.groupby(
        ["instrument_id", "base_ccy", "quote_ccy"], sort=False
    ):
        amounts = dict(zip(grp["ccy"], grp["amount"]))
        base_amt = float(amounts.get(base_ccy, 0.0))
        quote_amt = float(amounts.get(quote_ccy, 0.0))
        cross = "USD" not in (base_ccy, quote_ccy)

        commodity = base_ccy in COMMODITY_CCYS or quote_ccy in COMMODITY_CCYS
        if quote_ccy == "USD":
            usd_leg = quote_amt
            notional_base = _signed_magnitude(usd_leg, base_amt)
            # Dollar convention flips XXXUSD pairs only; a metal keeps its own sign.
            notional_usd = notional_base if commodity else usd_leg
        elif base_ccy == "USD":
            usd_leg = base_amt
            notional_base = usd_leg
            notional_usd = usd_leg
        else:
            rate = ccy_spot_map.get(base_ccy, float("nan"))
            notional_base = base_amt * rate
            notional_usd = notional_base

        rows.append({
            "instrument_id": instrument_id, "base_ccy": base_ccy, "quote_ccy": quote_ccy,
            "cross": cross, "commodity": commodity, "spot": pair_spot.get(instrument_id, float("nan")),
            "notional_base": notional_base, "notional_usd": notional_usd,
            "move_1pct_usd": notional_base * 0.01 if pd.notna(notional_base) else float("nan"),
        })

    df = pd.DataFrame(rows, columns=PAIR_COLUMNS)
    df = df.assign(_abs=df["notional_base"].abs()).sort_values(
        "_abs", ascending=False, kind="mergesort", na_position="last"
    ).drop(columns="_abs").reset_index(drop=True)
    return df


def pair_delta_totals(df: pd.DataFrame) -> dict:
    """Net/Gross USD summed over `per_pair_delta`'s own `notional_usd` column (the
    "dollar convention" total, +1 for USDXXX pairs / -1 for XXXUSD pairs, matching
    CLAUDE.md's "Net USD (FX only)" sign rule applied per pair instead of per currency).
    Commodity pairs (XAUUSD etc., `commodity` column) are excluded from this total for
    the same reason `engine.ladder.exposure.portfolio_totals` excludes them from FX
    Net/Gross -- CLAUDE.md reports gold separately -- but still returned in `df` itself
    for their own row in the table.

    This is a genuinely DIFFERENT number from `engine.ladder.exposure.portfolio_totals`
    net_usd/gross_usd (negated) whenever a cross is open: a cross contributes only its
    base currency's own USD-converted view here (see per_pair_delta's docstring), while
    portfolio_totals independently captures BOTH legs of a cross (e.g. EUR and SEK) via
    the per-currency delta table. Prefer portfolio_totals for the headline Net/Gross USD
    cards; this is the per-pair table's own total row only. NaN if any (non-commodity)
    pair's notional_usd is NaN (never estimated, never silently dropped)."""
    if df.empty:
        return {"net_usd": 0.0, "gross_usd": 0.0, "pairs": 0}
    fx_only = df.loc[~df["commodity"]] if "commodity" in df.columns else df
    return {
        "net_usd": float(fx_only["notional_usd"].sum(skipna=False)),
        "gross_usd": float(fx_only["notional_usd"].abs().sum(skipna=False)),
        "pairs": int(len(fx_only)),
    }
