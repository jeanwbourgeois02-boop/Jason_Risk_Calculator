"""Screenshot-style FX exposure ladder (docs/CASH_LADDER_SPEC.md).

Pure Python/pandas: no SQL, no schema, no market-data calls. This is the DELTA engine
for the cash ladder: a pure exposure table (currency x settlement date), never P&L.
It intentionally has no relationship to the workbook forward mark-to-market P&L in
engine/ladder/valuation.py or to any realised/unrealised ledger -- those are engine/pnl
concerns. `entry_rate`, where an adapter supplies it, is carried through to
`contributions` for drill-down display only; it plays no part in any computation here.

One record per trade LEG, not one per trade: a USD leg is priced like any other leg
(spot = 1.0 identity), so a cross such as EURSEK -- which has no USD leg at all -- is
not excluded. `trade_id` may repeat across a trade's own legs; the natural key is
(trade_id, currency, settlement_date).

The mark for delta is always SPOT, by design -- this ladder answers "how much of each
currency will move on which date", not "what did that move earn". Marking at a forward
outright, or netting against an entry rate, would turn this back into a P&L view; that
is deliberately out of scope here (see engine/pnl/ for LTD / daily / MTD / YTD P&L).

Formulas (per currency, summed over that currency's legs):
    Local delta  = sum of signed leg amounts in that currency
    USD delta    = Local delta x USD-per-local spot rate (USD identity = 1.0)

Net USD / Gross USD (portfolio_totals) exclude the USD currency row: Net = sum of
non-USD usd_delta, Gross = sum of |non-USD usd_delta|. A USD leg still contributes its
own row to the ladder and summary, it is just not double-counted as an "FX exposure"
against itself.

Rounding tolerance: USD figures are exact floats; callers compare to whole-USD
reference values with ROUNDING_TOLERANCE_USD. A larger difference is material.

Rate plausibility guard (2026-09-18, "krw is wrong by a factor of 1000"): the only
number this module takes on trust is the spot rate. A SPOT mark stored at the wrong
scale (1.39 for USDKRW instead of 1,390, or 1,390,000) would silently multiply that
currency's USD delta, Net/Gross USD and every scenario cell by 1,000 -- worse than a
blank. So each currency's USD-per-local rate is compared with the rate the book's own
FX fills imply for it (records carry `currency_pair` and `entry_rate`, the fill; for
USDKRW at 1,394 that is 1/1,394 USD per KRW). A rate more than RATE_PLAUSIBILITY_FACTOR
(100x) away from the fills' median is reported as SUSPECT_RATE with the mark, the pair
and the fill rate named, and that currency's USD delta is left NaN exactly like a
missing rate -- never used, never substituted. Forward points and any real move are
orders of magnitude inside the threshold; only a scale error can trip it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import pandas as pd

REQUIRED_FIELDS = ("trade_id", "settlement_date", "book", "currency", "local_amount")
RATE_FIELDS = ("rate", "inverted", "source", "timestamp", "stale")
ROUNDING_TOLERANCE_USD = 1.0

SUMMARY_COLUMNS = ["currency", "fx_rate", "local_delta", "usd_delta",
                   "rate_source", "rate_timestamp", "status"]
# Ratio between the official spot and the book's own fill rates beyond which a rate is
# reported SUSPECT_RATE rather than used (module docstring). 100x: a 1,000x scale error
# is caught, a 30 % devaluation is not.
RATE_PLAUSIBILITY_FACTOR = 100.0
_FX_PRODUCTS = frozenset({"FX_SPOT", "FX_FWD", "FX_SWAP"})
CONTRIBUTION_COLUMNS = ["settlement_date", "currency", "trade_id", "book", "local_amount"]
STATUS_COLUMNS = ["currency", "status", "message"]

# Troy-ounce metals that travel through this module as an ordinary "currency" (an
# XAUUSD leg's ccy is 'XAU', instruments.base_ccy = 'XAU' per CLAUDE.md) but are not FX:
# CLAUDE.md "Net USD (FX only) ... Gross USD ... Gold and equity futures are reported
# separately" (user complaint 2026-09-17, "the XAU does not work well" -- gold was being
# summed into FX Net/Gross like any other currency). `result.summary` / `result.ladder`
# still carry XAU as its own row/column (oz local_delta, USD notional at spot as
# usd_delta) -- nothing here drops it -- only `portfolio_totals`'s FX-only totals
# exclude it, same treatment as the USD row itself, just for a different reason (USD is
# excluded because it would be self-referential; a commodity is excluded because
# CLAUDE.md explicitly reports it apart from FX).
COMMODITY_CCYS = frozenset({"XAU", "XAG", "XPT", "XPD"})


@dataclass(frozen=True)
class ExposureResult:
    ladder: pd.DataFrame         # index settlement_date, columns currency, signed local amounts
    summary: pd.DataFrame        # SUMMARY_COLUMNS, one row per currency
    contributions: pd.DataFrame  # CONTRIBUTION_COLUMNS, one row per leg
    status: pd.DataFrame         # STATUS_COLUMNS: OK | STALE | MISSING_RATE per currency

    def cell_trades(self, settlement_date: str, currency: str) -> pd.DataFrame:
        c = self.contributions
        mask = (c["settlement_date"] == settlement_date) & (c["currency"] == currency)
        return c[mask].reset_index(drop=True)


def portfolio_totals(result: "ExposureResult", *, commodity_ccys: frozenset = COMMODITY_CCYS) -> dict:
    """Portfolio-level aggregates of the per-currency summary. Net = sum of non-USD,
    non-commodity usd_delta, Gross = sum of |non-USD, non-commodity usd_delta| (the USD
    currency row is excluded so a USD leg is not double-counted as its own "FX
    exposure"; `commodity_ccys`, default `COMMODITY_CCYS` = {XAU, XAG, XPT, XPD}, are
    excluded because CLAUDE.md reports gold/metals separately from FX Net/Gross -- see
    `commodities` below, not because they are self-referential). If any (non-commodity)
    currency lacks a rate the FX totals are NaN and `missing` names it; nothing is
    substituted. A commodity missing a rate never blocks the FX totals -- it is surfaced
    only in `commodities`. No P&L field here -- this is a delta table, not a ledger.

    `commodities`: one dict per commodity currency actually present in the summary,
    `{"currency", "local_delta" (e.g. troy ounces), "usd_delta" (NaN if no rate),
    "status"}` -- so a caller can render "XAU: 482 oz, $1,688,659" on its own line
    without it ever entering the FX totals above."""
    s = result.summary
    if s.empty:
        return {"net_usd": 0.0, "gross_usd": 0.0, "currencies": 0, "missing": [], "commodities": []}
    is_commodity = s["currency"].isin(commodity_ccys)
    fx_only = s.loc[(s["currency"] != "USD") & ~is_commodity]
    missing = sorted(fx_only.loc[fx_only["usd_delta"].isna(), "currency"])
    commodities = [
        {"currency": row["currency"], "local_delta": row["local_delta"],
         "usd_delta": row["usd_delta"], "status": row["status"]}
        for _, row in s.loc[is_commodity].iterrows()
    ]
    return {
        "net_usd": float(fx_only["usd_delta"].sum(skipna=False)),
        "gross_usd": float(fx_only["usd_delta"].abs().sum(skipna=False)),
        "currencies": int(len(s)),  # unchanged meaning: total currencies with any exposure (incl. USD/commodities)
        "missing": missing,
        "commodities": commodities,
    }


def ladder_usd_equivalent(result: "ExposureResult") -> pd.Series:
    """Spot USD value per settlement date: sum over currencies of local amount x that
    currency's spot rate from the summary. This is a funding/exposure view (how much
    USD each date's flows are worth today), NOT a P&L figure. NaN for a date where any
    non-zero amount has no rate. Index = settlement_date."""
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
    # Legs, not trades: trade_id repeats across a trade's own legs. The natural key
    # (trade_id, currency, settlement_date) catches accidental duplicate legs instead.
    dup = df.duplicated(subset=["trade_id", "currency", "settlement_date"])
    if dup.any():
        raise ValueError(f"duplicate (trade_id, currency, settlement_date): "
                         f"{sorted(map(tuple, df.loc[dup, ['trade_id', 'currency', 'settlement_date']].values))}")
    if df[["settlement_date", "currency", "local_amount"]].isna().any().any():
        raise ValueError("settlement_date, currency and local_amount must not be null")
    df["settlement_date"] = df["settlement_date"].astype(str)
    df["local_amount"] = df["local_amount"].astype(float)
    return df


def _validate_rates(rates: Mapping[str, Mapping[str, Any]]) -> None:
    for ccy, entry in rates.items():
        missing = [f for f in RATE_FIELDS if f not in entry]
        if missing:
            raise ValueError(f"rate entry for {ccy} missing {missing}")


_USD_IDENTITY = {"rate": 1.0, "inverted": False, "source": "identity", "timestamp": "", "stale": False}


def fill_implied_rates(df: pd.DataFrame) -> dict:
    """currency -> median USD-per-local rate implied by the book's own FX fills (module
    docstring, rate plausibility guard). Uses only FX leg records (`product_type` in
    FX_SPOT/FX_FWD/FX_SWAP) whose `currency_pair` is that currency against USD: USDXXX
    fills give 1/entry_rate, XXXUSD fills give entry_rate. Records without those fields
    (hand-built fixtures), crosses, option-delta records and non-positive fills are
    skipped, so a book with no usable fill for a currency simply has no entry here and
    the guard stays silent for it."""
    out: dict = {}
    needed = {"currency", "currency_pair", "entry_rate", "product_type"}
    if df.empty or not needed.issubset(df.columns):
        return out
    fx = df[df["product_type"].isin(_FX_PRODUCTS)]
    implied: dict = {}
    for ccy, pair, fill in zip(fx["currency"], fx["currency_pair"], fx["entry_rate"]):
        if ccy == "USD" or not isinstance(pair, str) or len(pair) != 6:
            continue
        try:
            fill = float(fill)
        except (TypeError, ValueError):
            continue
        if not fill > 0 or pd.isna(fill):
            continue
        base, quote = pair[:3], pair[3:]
        if base == "USD" and quote == ccy:
            implied.setdefault(ccy, []).append(1.0 / fill)
        elif quote == "USD" and base == ccy:
            implied.setdefault(ccy, []).append(fill)
    for ccy, values in implied.items():
        out[ccy] = float(pd.Series(values).median())
    return out


def _suspect_reason(ccy: str, entry: Mapping[str, Any], fx: float, implied: float) -> str:
    """'' when `fx` (USD per local) is within RATE_PLAUSIBILITY_FACTOR of the fill-implied
    rate, else the plain-English reason naming the mark as quoted and the fill rate."""
    if not implied > 0 or not fx > 0:
        return ""
    ratio = fx / implied
    if ratio < RATE_PLAUSIBILITY_FACTOR and ratio > 1.0 / RATE_PLAUSIBILITY_FACTOR:
        return ""
    factor = ratio if ratio >= 1 else 1.0 / ratio
    pair = entry.get("pair") or ""
    quoted = float(entry["rate"])
    # Show the fill the same way round as the mark so the two are directly comparable.
    fill_quoted = 1.0 / implied if entry["inverted"] else implied
    return (f"official SPOT {pair} {quoted:,.6g} is {factor:,.0f}x away from the book's own "
            f"{ccy} fills (~{fill_quoted:,.6g}); USD delta not computed -- check the SPOT mark's scale")


def build_exposure(records, rates: Mapping[str, Mapping[str, Any]], *,
                   books: Iterable[str] | None = None) -> ExposureResult:
    """Build ladder, summary, contributions and rate status from normalized records.

    `rates`: currency -> {rate, inverted, source, timestamp, stale}. USD is implicitly 1.0.
    A missing rate leaves usd_delta NaN and status MISSING_RATE; zero is never
    substituted. A stale rate is used but flagged STALE.
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
    implied_rates = fill_implied_rates(df)

    summary_rows, status_rows = [], []
    for ccy, grp in df.groupby("currency", sort=True):
        local_delta = float(grp["local_amount"].sum())
        entry = _USD_IDENTITY if ccy == "USD" else rates.get(ccy)
        fx = float("nan")
        source = timestamp = ""
        suspect = ""
        if entry is not None and ccy in implied_rates:
            suspect = _suspect_reason(ccy, entry, usd_per_local(entry), implied_rates[ccy])
        if suspect:
            # Rate plausibility guard (module docstring): a mark at the wrong scale is
            # reported, with the numbers, and treated exactly like a missing rate.
            source, timestamp = str(entry["source"]), str(entry["timestamp"])
            status, msg = "SUSPECT_RATE", suspect
        elif entry is None:
            # 2026-09-17 ("no bnp fall back" -- user decision): reworded from "no
            # market-data rate" -- this module only ever receives official SPOT rates
            # (rates_from_marks, marks_official) from its callers now, never a BNP
            # fallback, so "no official SPOT" is the accurate reason. This function is
            # date-agnostic (records/rates only), so the caller (which does know
            # as_of_date) may append it, e.g. ui/tabs/cash_ladder.py::net_gross_usd's
            # "no official SPOT for {as_of_date}: {ccys}".
            status, msg = "MISSING_RATE", f"no official SPOT for {ccy}; USD delta not computed"
        else:
            fx = usd_per_local(entry)
            source, timestamp = str(entry["source"]), str(entry["timestamp"])
            status = "STALE" if entry["stale"] else "OK"
            msg = f"stale rate from {source} at {timestamp}" if entry["stale"] else ""
        usd_delta = local_delta * fx
        summary_rows.append({"currency": ccy, "fx_rate": fx, "local_delta": local_delta,
                             "usd_delta": usd_delta, "rate_source": source,
                             "rate_timestamp": timestamp, "status": status})
        status_rows.append({"currency": ccy, "status": status, "message": msg})

    return ExposureResult(
        ladder=ladder,
        summary=pd.DataFrame(summary_rows, columns=SUMMARY_COLUMNS),
        contributions=contributions,
        status=pd.DataFrame(status_rows, columns=STATUS_COLUMNS),
    )
