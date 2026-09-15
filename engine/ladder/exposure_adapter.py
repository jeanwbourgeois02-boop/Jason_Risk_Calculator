"""Adapter: BNP parser output (data/ingest/bnp.py ParseResult) -> normalized records for
engine/ladder/exposure.build_exposure. Pure transformation; no DB, no marks, no UI.

Field mapping (verified against data/raw/HA_PNL_20260818.csv):
    trade_id          Trade.trade_id           = BNP id after '-' in Symbol (stable per BNP)
    trade_date        Trade.trade_date         = description 'TD'
    settlement_date   TradeLeg.settle_date     = description 'VD' (both legs share it)
    currency_pair     Trade.instrument_id      = first 6 chars of Symbol
    local currency    the non-USD leg's ccy
    local_amount      that leg's signed amount  (base leg = Quantity, quote leg = -Local Cost)
    entry_rate        TradeLeg.rate            = the fill/outright rate, carried for drill-down
                      display only -- it plays no part in any exposure.py computation (delta is
                      always marked at spot, never at the entry rate).
    book_source       Trade.strategy           = 'NM Strategy' (HAHY7 on every forward). The BNP
                      file has no 'HA' book field; Business Unit and Fund are both 'NMMF'.
    book              book_mapping.get(book_source, book_source). Default mapping is identity,
                      so book == 'HAHY7'; it is never silently renamed to 'HA'. Pass
                      book_mapping={'HAHY7': 'HA'} explicitly to reproduce the screenshot label.
    fund              constant 'NMMF' (parser filters Fund == NMMF)
    product_type      Trade.product            = always 'FX_FWD' from the parser.
    is_ndf / settles_cash  from the parser's NDF list (BRL, TWD, KRW, IDR). NDF records STAY in
                      the primary ladder (the reference screenshot shows BRL, which exists only
                      as NDFs in the source); settles_cash=0 is exposed so a cash-only view can
                      filter them, never dropped here.

One record per trade LEG, not per trade: a trade with no USD leg (a cross such as
EURSEK) used to have no way to carry a usd_entry_amount and was dropped whole into
`unresolved`; it now contributes one record per leg like every other trade, so the USD
leg is no longer required. engine/ladder/exposure.py is a pure delta table (no P&L),
so there is no usd_entry_amount to carry any more -- every leg, USD or not, is simply
priced at spot. Nothing is invented for crosses -- their legs are just priced at spot
like any other currency in engine/ladder/exposure.py.

Spot and swaps (audited on HA_PNL_20260818.csv, 2026-09-14): no row has TD == VD and no
row pair satisfies the CLAUDE.md swap package rule, so the source contains neither spot
trades nor linkable swap legs. The parser labels every FORWARD row FX_FWD and sets
package_id = trade_id. Nothing is invented here: each row is one independent forward.

Excluded: CURRENCY balance rows (they are positions, not trades, and never reach
ParseResult.trades), FUTURES (product != FX_*), parser rejects, non-FX products, and
any leg whose trade has no instrument record. Each exclusion is reported in
`unresolved`, never dropped silently.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from data.ingest.bnp import ParseResult

FX_PRODUCTS = frozenset({"FX_SPOT", "FX_FWD", "FX_SWAP"})
FUND = "NMMF"


@dataclass(frozen=True)
class Unresolved:
    trade_id: str
    symbol: str
    reason: str


DEFAULT_BOOK_MAPPING: Dict[str, str] = {}  # identity: book == NM Strategy source value


def records_from_parse(res: ParseResult,
                       book_mapping: Dict[str, str] | None = None) -> Tuple[List[dict], List[Unresolved]]:
    """Return (normalized records, unresolved rows). One record per trade LEG.

    ``book_mapping`` maps the source 'NM Strategy' value to a display book; unmapped
    values pass through unchanged."""
    mapping = DEFAULT_BOOK_MAPPING if book_mapping is None else book_mapping
    legs_by_trade: Dict[str, list] = {}
    for leg in res.legs:
        legs_by_trade.setdefault(leg.trade_id, []).append(leg)

    records: List[dict] = []
    unresolved: List[Unresolved] = [
        Unresolved(trade_id="", symbol=r.symbol, reason=f"parser reject (row {r.row_no}): {r.reason}")
        for r in res.rejects
    ]
    for t in res.trades:
        if t.product not in FX_PRODUCTS:
            unresolved.append(Unresolved(t.trade_id, t.instrument_id, f"non-FX product {t.product} excluded"))
            continue
        instrument = res.instruments.get(t.instrument_id)
        if instrument is None:
            unresolved.append(Unresolved(t.trade_id, t.instrument_id, "no instrument for leg currency"))
            continue
        legs = legs_by_trade.get(t.trade_id, [])
        if not legs:
            unresolved.append(Unresolved(t.trade_id, t.instrument_id, "no legs found for trade"))
            continue
        for leg in legs:
            records.append({
                "trade_id": t.trade_id,
                "source_row_id": t.trade_id,
                "product_type": t.product,
                "symbol": f"{t.instrument_id}-{t.trade_id}",
                "symbol_description": t.description,
                "trade_date": t.trade_date,
                "settlement_date": leg.settle_date,
                "currency_pair": t.instrument_id,
                "currency": leg.ccy,
                "local_amount": float(leg.amount),
                "entry_rate": float(leg.rate),
                "book_source": t.strategy,
                "book": mapping.get(t.strategy, t.strategy),
                "account": t.account,
                "fund": FUND,
                "strategy": t.strategy,
                "is_ndf": int(instrument.is_ndf),
                "settles_cash": int(leg.settles_cash),
            })
    return records, unresolved


_DB_SQL = """
SELECT t.trade_id, t.product, t.instrument_id, t.description, t.trade_date, t.price,
       t.strategy, t.account, i.is_ndf,
       l.ccy, l.amount, l.settle_date, l.settles_cash
FROM trades t JOIN instruments i USING (instrument_id) JOIN trade_legs l USING (trade_id)
WHERE t.trade_date <= :as_of AND l.settle_date >= :as_of
ORDER BY t.trade_id, l.leg_no
"""


def records_from_db(conn, as_of_date: str,
                    book_mapping: Dict[str, str] | None = None) -> Tuple[List[dict], List[Unresolved]]:
    """Same records as records_from_parse, read back from the SQLite tables the upload
    flow populates (trades / trade_legs / instruments). Read-only; no schema change.
    Open trades only: trade_date <= as_of and settle_date >= as_of (matches the cash
    ladder's `>=` rule). Field derivations are identical to records_from_parse: one
    record per leg, priced at spot only (no P&L, no usd_entry_amount)."""
    mapping = DEFAULT_BOOK_MAPPING if book_mapping is None else book_mapping
    by_trade: Dict[str, list] = {}
    for r in conn.execute(_DB_SQL, {"as_of": as_of_date}).fetchall():
        by_trade.setdefault(r[0], []).append(r)
    records: List[dict] = []
    unresolved: List[Unresolved] = []
    for trade_id, legs in by_trade.items():
        product, pair, desc, trade_date, price, strategy, account, is_ndf = legs[0][1:9]
        if product not in FX_PRODUCTS:
            unresolved.append(Unresolved(trade_id, pair, f"non-FX product {product} excluded"))
            continue
        for leg in legs:
            ccy, amount, settle_date, settles_cash = leg[9], leg[10], leg[11], leg[12]
            records.append({
                "trade_id": trade_id, "source_row_id": trade_id, "product_type": product,
                "symbol": f"{pair}-{trade_id}", "symbol_description": desc,
                "trade_date": trade_date, "settlement_date": settle_date, "currency_pair": pair,
                "currency": ccy, "local_amount": float(amount), "entry_rate": float(price),
                "book_source": strategy, "book": mapping.get(strategy, strategy),
                "account": account, "fund": FUND, "strategy": strategy,
                "is_ndf": int(is_ndf), "settles_cash": int(settles_cash),
            })
    return records, unresolved
