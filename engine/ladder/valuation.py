"""Trace workbook P&L to each trade's signed local and USD entry legs."""
from __future__ import annotations

import sqlite3
import pandas as pd

from engine.ladder.ladder import spot_table
from engine.pnl.pnl import ltd_per_trade

DETAIL_COLUMNS = [
    "settle_date", "ccy", "trade_id", "instrument_id", "settlement", "local_amount",
    "other_ccy", "other_amount", "fill", "valuation_date", "mark", "spot_usd_per_local",
    "usd_entry", "usd_valuation", "physical_usd_valuation", "valuation_residual",
    "workbook_quantity", "denominator", "pnl_usd", "status",
]


def ladder_trade_valuation(conn: sqlite3.Connection, as_of_date: str,
                           source: str | None = None) -> pd.DataFrame:
    """Open forwards only. Cash balances have no invented cost basis or P&L.

    usd_valuation is the workbook value before adding the signed USD entry.
    physical_usd_valuation uses the actual PB local leg; any rounding difference
    is disclosed separately, never used to modify workbook P&L.
    NDF amounts are notionals here and remain excluded from cash settlement flows.
    """
    priced = ltd_per_trade(conn, as_of_date, source=source, strict=False)
    forward_ids = {row[0] for row in conn.execute("SELECT trade_id FROM trades WHERE product='FX_FWD'")}
    priced = priced[priced["trade_id"].isin(forward_ids) & (priced["settle_date"] >= as_of_date)]
    if priced.empty:
        return pd.DataFrame(columns=DETAIL_COLUMNS)
    legs = pd.read_sql_query("SELECT trade_id, ccy, amount FROM trade_legs", conn)
    instruments = pd.read_sql_query(
        "SELECT instrument_id, base_ccy, quote_ccy, is_ndf FROM instruments", conn
    ).set_index("instrument_id")
    spot = spot_table(conn, as_of_date, source=source).set_index("ccy")["spot"].to_dict()
    rows = []
    for trade in priced.to_dict("records"):
        instrument = instruments.loc[trade["instrument_id"]]
        base, quote = instrument["base_ccy"], instrument["quote_ccy"]
        ccy = quote if base == "USD" else base
        amounts = legs[legs["trade_id"] == trade["trade_id"]].groupby("ccy")["amount"].sum()
        local = amounts.get(ccy, float("nan"))
        usd_entry = amounts.get("USD", float("nan"))
        cross = "USD" not in (base, quote)
        rate = trade["mark"]
        physical = float("nan")
        if pd.notna(rate) and rate > 0 and not cross:
            physical = local / rate if base == "USD" else local * rate
        valuation = trade["pnl_usd"] - usd_entry
        status = "Priced"
        if pd.isna(trade["pnl_usd"]):
            status = "Missing workbook rate or USD entry"
        if cross:
            status = "Workbook cross formula; no actual USD entry leg" if pd.notna(trade['pnl_usd']) else "Cross: missing workbook rate"
        rows.append({
            **trade, "ccy": ccy, "local_amount": local,
            "other_ccy": quote if cross else "",
            "other_amount": amounts.get(quote, float("nan")) if cross else float("nan"),
            "settlement": "NDF notionals (not cash flows)" if instrument["is_ndf"] else "Deliverable",
            "spot_usd_per_local": spot.get(ccy, float("nan")),
            "usd_entry": usd_entry, "usd_valuation": valuation,
            "physical_usd_valuation": physical, "valuation_residual": valuation - physical,
            "status": status,
        })
    return pd.DataFrame(rows).reindex(columns=DETAIL_COLUMNS).sort_values(
        ["settle_date", "ccy", "trade_id"], kind="mergesort"
    ).reset_index(drop=True)


def ladder_valuation_summary(detail: pd.DataFrame) -> pd.DataFrame:
    """Currency/date/settlement totals; incomplete groups remain explicitly unpriced.

    Entry and valuation rates are deliberately retained at trade level: averaging
    rates hides distinct entry economics and can change the Excel result.
    """
    keys = ["settle_date", "ccy", "settlement"]
    amounts = ["local_amount", "usd_entry", "usd_valuation", "pnl_usd"]
    columns = keys + ["trades", "missing_prices"] + amounts + ["status"]
    if detail.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    for group_key, group in detail.groupby(keys, sort=True):
        missing = int(group["pnl_usd"].isna().sum())
        row = dict(zip(keys, group_key))
        row.update(trades=len(group), missing_prices=missing,
                   status=f"Unpriced: {missing} of {len(group)} trades" if missing else "Priced")
        for col in amounts:
            row[col] = group[col].sum(skipna=False)
        rows.append(row)
    return pd.DataFrame(rows, columns=columns)
