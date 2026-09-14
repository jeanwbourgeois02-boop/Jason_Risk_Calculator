"""Shared display-only number formatting for Dash tables.

Factored out of ``ui/tabs/cash_ladder.py`` once ``ui/tabs/pnl.py`` needed the same
convention. No P&L/rounding logic beyond display lives here -- per CLAUDE.md ("ui/ ...
never recomputes P&L or delta itself"), storage/precision stay in engine/ and data/.
"""
from __future__ import annotations

import pandas as pd


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
