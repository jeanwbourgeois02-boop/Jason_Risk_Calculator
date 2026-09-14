"""Adapter: BNP parser output (data/ingest/bnp.py ParseResult) -> normalized records for
engine/ladder/exposure.build_exposure. Pure transformation; no DB, no marks, no UI.

Field mapping (verified against data/raw/HA_PNL_20260818.csv):
    trade_id          Trade.trade_id           = BNP id after '-' in Symbol (stable per BNP)
    trade_date        Trade.trade_date         = description 'TD'
    settlement_date   TradeLeg.settle_date     = description 'VD' (both legs share it)
    currency_pair     Trade.instrument_id      = first 6 chars of Symbol
    local currency    the non-USD leg's ccy
    local_amount      that leg's signed amount  (base leg = Quantity, quote leg = -Local Cost)
    usd_entry_amount  -(USD leg amount)        = USD notional carrying the local leg's sign
                      (broker_reference convention: sold AUD -> AUD < 0 and USD entry < 0,
                      matching the screenshot fixture). Every trade keeps its own rate.
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

Spot and swaps (audited on HA_PNL_20260818.csv, 2026-09-14): no row has TD == VD and no
row pair satisfies the CLAUDE.md swap package rule, so the source contains neither spot
trades nor linkable swap legs. The parser labels every FORWARD row FX_FWD and sets
package_id = trade_id. Nothing is invented here: each row is one independent forward.

Excluded: CURRENCY balance rows (they are positions, not trades, and never reach
ParseResult.trades), FUTURES (product != FX_*), parser rejects, and any FX trade
without exactly one USD leg (crosses such as EURSEK have no USD entry leg). Each
exclusion is reported in `unresolved`, never dropped silently.
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
    """Return (normalized records, unresolved rows). One record per FX trade.

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
        legs = legs_by_trade.get(t.trade_id, [])
        usd = [l for l in legs if l.ccy == "USD"]
        local = [l for l in legs if l.ccy != "USD"]
        if len(legs) != 2 or len(usd) != 1 or len(local) != 1:
            unresolved.append(Unresolved(t.trade_id, t.instrument_id,
                                         f"expected one USD leg and one local leg, got {[l.ccy for l in legs]}"))
            continue
        if usd[0].settle_date != local[0].settle_date:
            unresolved.append(Unresolved(t.trade_id, t.instrument_id, "legs settle on different dates"))
            continue
        records.append({
            "trade_id": t.trade_id,
            "source_row_id": t.trade_id,
            "product_type": t.product,
            "symbol": f"{t.instrument_id}-{t.trade_id}",
            "symbol_description": t.description,
            "trade_date": t.trade_date,
            "settlement_date": local[0].settle_date,
            "currency_pair": t.instrument_id,
            "currency": local[0].ccy,
            "local_amount": float(local[0].amount),
            "usd_entry_amount": -float(usd[0].amount),
            "entry_rate": float(t.price),
            "book_source": t.strategy,
            "book": mapping.get(t.strategy, t.strategy),
            "account": t.account,
            "fund": FUND,
            "strategy": t.strategy,
            "is_ndf": int(res.instruments[t.instrument_id].is_ndf),
            "settles_cash": int(local[0].settles_cash),
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
    ladder's `>=` rule). Field derivations are identical to records_from_parse."""
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
        usd = [l for l in legs if l[9] == "USD"]
        local = [l for l in legs if l[9] != "USD"]
        if len(legs) != 2 or len(usd) != 1 or len(local) != 1:
            unresolved.append(Unresolved(trade_id, pair,
                                         f"expected one USD leg and one local leg, got {[l[9] for l in legs]}"))
            continue
        if usd[0][11] != local[0][11]:
            unresolved.append(Unresolved(trade_id, pair, "legs settle on different dates"))
            continue
        records.append({
            "trade_id": trade_id, "source_row_id": trade_id, "product_type": product,
            "symbol": f"{pair}-{trade_id}", "symbol_description": desc,
            "trade_date": trade_date, "settlement_date": local[0][11], "currency_pair": pair,
            "currency": local[0][9], "local_amount": float(local[0][10]),
            "usd_entry_amount": -float(usd[0][10]), "entry_rate": float(price),
            "book_source": strategy, "book": mapping.get(strategy, strategy),
            "account": account, "fund": FUND, "strategy": strategy,
            "is_ndf": int(is_ndf), "settles_cash": int(local[0][12]),
        })
    return records, unresolved
