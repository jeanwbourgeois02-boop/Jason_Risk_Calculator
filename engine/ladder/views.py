"""Cash ladder pivoted for display. See CLAUDE.md "Six tabs as views" -> Cash ladder.

Built on top of engine.ladder.ladder.cash_ladder / spot_table / convert_to_usd; adds no
new SQL of its own.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

import pandas as pd

from engine.ladder.ladder import cash_ladder, spot_table, convert_to_usd


def ladder_table(conn: sqlite3.Connection, as_of_date: str, source: Optional[str] = None) -> pd.DataFrame:
    """Cash ladder pivoted: one row per currency, one column per settle_date, plus
    `total` and `usd`.

    Ladder rows (LEG + CASH from `cash_ladder(conn, as_of_date, source='BNP')`, i.e. the
    'BNP' positions source -- this is a different `source` from the one this function's
    own `source` parameter forwards to `spot_table`, see below) are summed by
    (ccy, settle_date) across LEG and CASH kinds -- the pivot merges kinds, since the
    display is "how much of this currency moves on this date", not a breakdown by kind
    (use `cash_ladder` directly for the kind-level detail).

    Cells: local-currency amount for that (ccy, settle_date); 0.0 where a currency has
    no flow on a given date (an explicit zero, not blank/NaN -- distinguishes "no flow"
    from "unknown", and keeps `total` a plain row sum with no NaN-skipping ambiguity).

    Columns, in order: `ccy`, one column per ISO settle-date string appearing anywhere
    in the ladder (ascending), `total`, `usd`.
      - `total` = row sum of the local-amount columns (always defined, since cells are
        never NaN).
      - `usd` = `total x spot`, i.e. `convert_to_usd` applied to the per-currency totals,
        NOT the per-cell USD amounts summed. With a single spot rate per currency (no FX
        forward curve involved -- ladder amounts are undiscounted nominal cashflows) the
        two are numerically identical: sum_i(amount_i x spot) == (sum_i amount_i) x
        spot. Computing it once on the total avoids doing the same multiplication once
        per date column for no benefit.
      - USD rows convert at 1.0. Currencies with no official SPOT mark (see
        engine.ladder.ladder.spot_table) get `usd = NaN` -- never an estimated rate.

    ``source``: forwarded to `spot_table` (None = marks_official, the correct display
    path; an explicit source such as 'BNP_BVAL' restricts the SPOT lookup to that one
    raw-marks source, reconciliation only -- see CLAUDE.md "Official marks": BNP_BVAL is
    never official). Does not affect which ladder rows are pulled: those always come
    from `cash_ladder(conn, as_of_date, source='BNP')` (the raw PB snapshot), since
    `cash_ladder`'s own `source` parameter selects a `positions` source (BNP vs CALC),
    an unrelated axis.

    Row order: currencies with a defined `usd` sorted by |usd| descending first, then
    currencies with `usd` = NaN afterwards sorted alphabetically by `ccy` (stable order
    within each group).
    """
    ladder = cash_ladder(conn, as_of_date, source="BNP")

    grouped = ladder.groupby(["ccy", "settle_date"], as_index=False)["amount"].sum()

    if grouped.empty:
        pivot = pd.DataFrame({"ccy": []})
    else:
        pivot = grouped.pivot(index="ccy", columns="settle_date", values="amount")
        pivot = pivot.fillna(0.0)
        pivot = pivot[sorted(pivot.columns)]
        pivot = pivot.reset_index()

    date_cols = [c for c in pivot.columns if c != "ccy"]
    pivot["total"] = pivot[date_cols].sum(axis=1) if date_cols else 0.0

    spot = spot_table(conn, as_of_date, source=source)
    totals = pivot[["ccy", "total"]].rename(columns={"total": "amount"})
    with_usd = convert_to_usd(totals, spot)
    pivot["usd"] = with_usd["amount_usd"]

    has_usd = pivot["usd"].notna()
    with_usd_rows = pivot[has_usd].assign(_abs_usd=lambda d: d["usd"].abs())
    with_usd_rows = with_usd_rows.sort_values(
        "_abs_usd", ascending=False, kind="mergesort"
    ).drop(columns="_abs_usd")
    without_usd_rows = pivot[~has_usd].sort_values("ccy", kind="mergesort")

    out = pd.concat([with_usd_rows, without_usd_rows], ignore_index=True)
    out = out[["ccy"] + date_cols + ["total", "usd"]]
    return out
