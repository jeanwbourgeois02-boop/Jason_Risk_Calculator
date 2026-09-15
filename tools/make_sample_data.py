"""Build data/sample/HA_PNL_SAMPLE_20260818.csv from the real BNP file.

The real file (data/raw/, git-ignored) holds the fund's actual positions and never goes
to GitHub. The sample keeps the exact BNP layout (all 137 columns) but:
  - takes two forwards per pair, every cash row, the futures row and one IRS row;
  - scales every amount column of a row by a fixed factor (rates, prices and Fx are
    untouched, so BNP's own arithmetic identities still hold and the import checks pass);
  - replaces trade ids and the trader name.
Market rates in the sample are real historical rates; amounts are not real positions.

Usage:  py -3 tools/make_sample_data.py   (needs data/raw/HA_PNL_20260818.csv)
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "raw" / "HA_PNL_20260818.csv"
OUT = ROOT / "data" / "sample" / "HA_PNL_SAMPLE_20260818.csv"

FACTORS = (0.35, 0.6, 0.45, 0.8, 0.25, 0.5)
KEEP_AS_IS = {"Price", "Fx", "Trade Factor", "FiscalYearEndMonth", "FSV LEVEL", "ALT_SRC", "CUSIP",
              "Column 2", "Unnamed: 136", "BB Industry Group", "BB Industry Subgroup", "ISIN", "LOANXID",
              "Primary Exchange", "Related Security", "SEDOL", "Side Pocket", "Total Return Swap",
              "Ultimate Issuer", "Ultimate Issuer Description", "Prior Day NAV Estimate",
              "Prior Month End NAV Estimate", "Prior Year End NAV Estimate", "Prior Month End Price",
              "Prior Year End Price", "PB Country Desc", "Executing Counterparty"}


def amount_columns(df: pd.DataFrame) -> list:
    return [c for c in df.select_dtypes("number").columns if c not in KEEP_AS_IS and "%" not in c]


def main() -> None:
    df = pd.read_csv(SRC)
    fwd = df[df["Financial Type"] == "FORWARD"]
    picked = fwd.groupby(fwd["Symbol"].str[:6], sort=False).head(2)
    others = pd.concat([df[df["Financial Type"] == "CURRENCY"],
                        df[df["Financial Type"] == "FUTURES"],
                        df[df["Financial Type"] == "INTEREST_RATE_SWAP"].head(1)])
    sample = pd.concat([picked, others]).sort_index().reset_index(drop=True)

    cols = amount_columns(sample)
    next_id = 900000001
    for i in sample.index:
        row = sample.loc[i]
        if row["Financial Type"] == "FUTURES":
            factor = 10 / row["Quantity"]            # 27 contracts -> 10 contracts, everything else pro rata
        else:
            factor = FACTORS[i % len(FACTORS)]
        sample.loc[i, cols] = row[cols] * factor
        if row["Financial Type"] == "FORWARD":
            sample.loc[i, "Local Cost"] = round(sample.loc[i, "Local Cost"])   # BNP rounds to whole quote units
            sample.loc[i, "Symbol"] = re.sub(r"-\d+$", f"-{next_id}", row["Symbol"])
            next_id += 1
        if row["Financial Type"] == "INTEREST_RATE_SWAP":
            sample.loc[i, "Symbol"] = "IRSOIS-USD-90000001"
    sample["Trader Name"] = "Sample Trader"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    sample.to_csv(OUT, index=False)
    print(f"wrote {OUT} ({len(sample)} rows: {sample['Financial Type'].value_counts().to_dict()})")


if __name__ == "__main__":
    main()
