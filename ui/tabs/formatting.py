"""Shared display-only number formatting for Dash tables.

Factored out of ``ui/tabs/cash_ladder.py`` once ``ui/tabs/pnl.py`` needed the same
convention. No P&L/rounding logic beyond display lives here -- per CLAUDE.md ("ui/ ...
never recomputes P&L or delta itself"), storage/precision stay in engine/ and data/.
"""
from __future__ import annotations

import pandas as pd
from dash import dash_table


def format_cell(value) -> str:
    """Format a single numeric cell: round to whole units, thousands separators,
    negatives in parentheses, blank ("") for NaN/None."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    rounded = round(float(value))
    if rounded < 0:
        return f"({abs(rounded):,})"
    return f"{rounded:,}"


def valuation_table(df: pd.DataFrame, table_id: str) -> dash_table.DataTable:
    """Preserve rate precision and text labels while formatting dollar amounts.
    Relocated from the (deleted 2026-09-16) Reconciliation tab -- still used by
    tests/test_cash_ladder_parity.py to render engine/ladder/valuation.py's output,
    which is live ladder-valuation logic, not a Reconciliation-only concern."""
    rates = {"fill", "mark", "spot_usd_per_local", "denominator"}
    labels = {
        "ccy": "Local currency", "settle_date": "Settlement date",
        "local_amount": "Signed local amount", "fill": "Entry FX",
        "mark": "Workbook valuation FX", "valuation_date": "Workbook pricing date",
        "usd_entry": "Signed USD entry", "usd_valuation": "Workbook USD valuation",
        "pnl_usd": "USD P&L", "spot_usd_per_local": "General spot (USD/local; reference)",
        "physical_usd_valuation": "Actual local leg at valuation FX (USD)",
        "valuation_residual": "Workbook less actual leg value (USD)",
        "denominator": "Workbook divisor", "workbook_quantity": "Workbook quantity C",
    }
    formatted = df.copy()
    for col in formatted:
        if col in rates:
            formatted[col] = formatted[col].map(lambda value: "" if pd.isna(value) else f"{value:,.8f}")
        elif pd.api.types.is_numeric_dtype(formatted[col]):
            formatted[col] = formatted[col].map(format_cell)
    return dash_table.DataTable(
        id=table_id,
        columns=[{"name": labels.get(col, col.replace("_", " ").title()), "id": col} for col in formatted],
        data=formatted.to_dict("records"),
        style_table={"overflowX": "auto"},
        style_cell={"textAlign": "right", "fontFamily": "monospace", "minWidth": "110px"},
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
        page_size=25,
    )


def format_frame(df: pd.DataFrame, label_col: str = "ccy") -> pd.DataFrame:
    """Apply `format_cell` to every column except `label_col` (the non-numeric row
    label, e.g. `ccy` or `settle_date`). Returns a new DataFrame of strings so it can be
    unit-tested without Dash."""
    out = df.copy()
    for col in out.columns:
        if col == label_col:
            continue
        out[col] = out[col].map(format_cell)
    return out
