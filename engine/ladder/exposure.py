"""Screenshot-style FX exposure ladder (docs/CASH_LADDER_SPEC.md).

Pure Python/pandas: no SQL, no schema, no market-data calls. This is an ADDITIONAL,
separately labelled "Exposure P&L" (Local delta x live spot - trade-level USD entry).
It does not replace the workbook forward mark-to-market P&L in engine/ladder/valuation.py.

Formulas (per currency):
    Local delta      = sum of signed local amounts
    USD delta        = Local delta x USD-per-local rate
    USD delta entry  = sum of trade-level usd_entry_amount (no averaging, no FIFO)
    Exposure P&L     = USD delta - USD delta entry

Rounding tolerance: USD figures are exact floats; callers compare to whole-USD
reference values with ROUNDING_TOLERANCE_USD. A larger difference is material.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import pandas as pd

REQUIRED_FIELDS = ("trade_id", "settlement_date", "book", "currency", "local_amount", "usd_entry_amount")
RATE_FIELDS = ("rate", "inverted", "source", "timestamp", "stale")
ROUNDING_TOLERANCE_USD = 1.0

SUMMARY_COLUMNS = ["currency", "fx_rate", "local_delta", "usd_delta", "usd_delta_entry",
                   "exposure_pnl", "rate_source", "rate_timestamp", "status"]
CONTRIBUTION_COLUMNS = ["settlement_date", "currency", "trade_id", "book", "local_amount", "usd_entry_amount"]
STATUS_COLUMNS = ["currency", "status", "message"]


@dataclass(frozen=True)
class ExposureResult:
    ladder: pd.DataFrame         # index settlement_date, columns currency, signed local amounts
    summary: pd.DataFrame        # SUMMARY_COLUMNS, one row per currency
    contributions: pd.DataFrame  # CONTRIBUTION_COLUMNS, one row per trade
    status: pd.DataFrame         # STATUS_COLUMNS: OK | STALE | MISSING_RATE per currency

    def cell_trades(self, settlement_date: str, currency: str) -> pd.DataFrame:
        c = self.contributions
        mask = (c["settlement_date"] == settlement_date) & (c["currency"] == currency)
        return c[mask].reset_index(drop=True)


def portfolio_totals(result: "ExposureResult") -> dict:
    """Portfolio-level aggregates of the per-currency summary (spec: Net = sum of USD
    deltas, Gross = sum of |USD delta|, Exposure P&L = sum of currency P&L). If any
    currency lacks a rate the USD totals are NaN and `missing` names it; nothing is
    substituted."""
    s = result.summary
    missing = sorted(s.loc[s["usd_delta"].isna(), "currency"]) if not s.empty else []
    if s.empty:
        return {"net_usd": float("nan"), "gross_usd": float("nan"), "exposure_pnl": float("nan"),
                "currencies": 0, "missing": []}
    return {
        "net_usd": float(s["usd_delta"].sum(skipna=False)),
        "gross_usd": float(s["usd_delta"].abs().sum(skipna=False)),
        "exposure_pnl": float(s["exposure_pnl"].sum(skipna=False)),
        "currencies": int(len(s)),
        "missing": missing,
    }


def ladder_usd_equivalent(result: "ExposureResult") -> pd.Series:
    """USD equivalent per settlement date: sum over currencies of local amount x that
    currency's fx_rate from the summary. NaN for a date where any non-zero amount has
    no rate. Index = settlement_date."""
    if result.ladder.empty:
        return pd.Series(dtype=float, name="usd_equivalent")
    fx = result.summary.set_index("currency")["fx_rate"]
    out = {}
    for day, row in result.ladder.iterrows():
        total = 0.0
        for ccy, amount in row.items():
            if amount == 0:
                continue
            rate = fx.get(ccy, float("nan"))
            if pd.isna(rate):
                total = float("nan")
                break
            total += amount * rate
        out[day] = total
    return pd.Series(out, name="usd_equivalent")


def usd_per_local(entry: Mapping[str, Any]) -> float:
    """Normalise a quoted rate to USD per unit of local currency using the explicit
    `inverted` flag (AUDUSD 0.70 -> 0.70; USDJPY 150 inverted -> 1/150). Never inferred."""
    rate = entry["rate"]
    if rate is None or pd.isna(rate) or rate <= 0:
        raise ValueError(f"rate must be positive, got {rate!r}")
    return 1.0 / float(rate) if entry["inverted"] else float(rate)


def _records_frame(records) -> pd.DataFrame:
    df = records.copy() if isinstance(records, pd.DataFrame) else pd.DataFrame(list(records))
    if df.empty and not len(df.columns):
        df = pd.DataFrame(columns=list(REQUIRED_FIELDS))  # no trades: empty, not an error
    missing = [f for f in REQUIRED_FIELDS if f not in df.columns]
    if missing:
        raise ValueError(f"records missing fields: {missing}")
    if df.empty:
        return df
    dup = df["trade_id"].duplicated()
    if dup.any():
        raise ValueError(f"duplicate trade_id: {sorted(df.loc[dup, 'trade_id'])}")
    if df[["settlement_date", "currency", "local_amount", "usd_entry_amount"]].isna().any().any():
        raise ValueError("settlement_date, currency, local_amount and usd_entry_amount must not be null")
    df["settlement_date"] = df["settlement_date"].astype(str)
    df["local_amount"] = df["local_amount"].astype(float)
    df["usd_entry_amount"] = df["usd_entry_amount"].astype(float)
    return df


def _validate_rates(rates: Mapping[str, Mapping[str, Any]]) -> None:
    for ccy, entry in rates.items():
        missing = [f for f in RATE_FIELDS if f not in entry]
        if missing:
            raise ValueError(f"rate entry for {ccy} missing {missing}")


_USD_IDENTITY = {"rate": 1.0, "inverted": False, "source": "identity", "timestamp": "", "stale": False}


def build_exposure(records, rates: Mapping[str, Mapping[str, Any]], *,
                   books: Iterable[str] | None = None) -> ExposureResult:
    """Build ladder, summary, contributions and rate status from normalized records.

    `rates`: currency -> {rate, inverted, source, timestamp, stale}. USD is implicitly 1.0.
    A missing rate leaves usd_delta / exposure_pnl NaN and status MISSING_RATE; zero is
    never substituted. A stale rate is used but flagged STALE.
    """
    _validate_rates(rates)
    df = _records_frame(records)
    if books is not None and not df.empty:
        df = df[df["book"].isin(set(books))]
    df = df.sort_values(["settlement_date", "currency", "trade_id"], kind="mergesort").reset_index(drop=True)

    contributions = df.reindex(columns=CONTRIBUTION_COLUMNS)
    if df.empty:
        ladder = pd.DataFrame()
    else:
        ladder = df.pivot_table(index="settlement_date", columns="currency", values="local_amount",
                                aggfunc="sum", fill_value=0.0).sort_index()
    ladder.index.name, ladder.columns.name = "settlement_date", None

    summary_rows, status_rows = [], []
    for ccy, grp in df.groupby("currency", sort=True):
        local_delta = float(grp["local_amount"].sum())
        usd_entry = float(grp["usd_entry_amount"].sum())
        entry = _USD_IDENTITY if ccy == "USD" else rates.get(ccy)
        fx = float("nan")
        source = timestamp = ""
        if entry is None:
            status, msg = "MISSING_RATE", f"no market-data rate for {ccy}; USD delta not computed"
        else:
            fx = usd_per_local(entry)
            source, timestamp = str(entry["source"]), str(entry["timestamp"])
            status = "STALE" if entry["stale"] else "OK"
            msg = f"stale rate from {source} at {timestamp}" if entry["stale"] else ""
        usd_delta = local_delta * fx
        summary_rows.append({"currency": ccy, "fx_rate": fx, "local_delta": local_delta,
                             "usd_delta": usd_delta, "usd_delta_entry": usd_entry,
                             "exposure_pnl": usd_delta - usd_entry, "rate_source": source,
                             "rate_timestamp": timestamp, "status": status})
        status_rows.append({"currency": ccy, "status": status, "message": msg})

    return ExposureResult(
        ladder=ladder,
        summary=pd.DataFrame(summary_rows, columns=SUMMARY_COLUMNS),
        contributions=contributions,
        status=pd.DataFrame(status_rows, columns=STATUS_COLUMNS),
    )
