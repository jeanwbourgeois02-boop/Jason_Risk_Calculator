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


_DB_SQL_GRID = """
SELECT t.trade_id, t.product, t.instrument_id, t.description, t.trade_date, t.price,
       t.strategy, t.account, i.is_ndf,
       l.ccy, l.amount, l.settle_date, l.settles_cash
FROM trades_official t JOIN instruments i USING (instrument_id) JOIN trade_legs l USING (trade_id)
WHERE t.trade_date <= :as_of AND l.settle_date >= :as_of
ORDER BY t.trade_id, l.leg_no
"""

# CLAUDE.md "Six tabs as views": the grid uses settle_date >= as_of (a leg settling
# today is cash that moves today), but delta/exposure aggregation (Net USD, Gross USD,
# per-currency delta -> anything that feeds build_exposure/summary/portfolio_totals)
# must use settle_date > as_of (a leg settling on as_of carries no delta by close,
# matching BNP dropping settled forwards -- mirrors engine/ladder/futures_delta.py).
_DB_SQL_EXPOSURE = """
SELECT t.trade_id, t.product, t.instrument_id, t.description, t.trade_date, t.price,
       t.strategy, t.account, i.is_ndf,
       l.ccy, l.amount, l.settle_date, l.settles_cash
FROM trades_official t JOIN instruments i USING (instrument_id) JOIN trade_legs l USING (trade_id)
WHERE t.trade_date <= :as_of AND l.settle_date > :as_of
ORDER BY t.trade_id, l.leg_no
"""

# Backward-compatible alias: historically the only query this module ran.
_DB_SQL = _DB_SQL_GRID


def records_from_db(conn, as_of_date: str,
                    book_mapping: Dict[str, str] | None = None, *,
                    for_exposure: bool = False) -> Tuple[List[dict], List[Unresolved]]:
    """Same records as records_from_parse, read back from the SQLite tables the upload
    flow populates (trades / trade_legs / instruments). Read-only; no schema change.

    ``for_exposure=False`` (default): grid rule, trade_date <= as_of and
    settle_date >= as_of (matches the cash ladder grid's `>=` rule) -- use this for the
    Cash ladder tab display.

    ``for_exposure=True``: delta rule, trade_date <= as_of and settle_date > as_of. Use
    this for anything that feeds engine.ladder.exposure.build_exposure /
    portfolio_totals / summary (Net USD, Gross USD, per-currency delta): a leg settling
    exactly on as_of carries no delta by close of that day (CLAUDE.md "Six tabs as
    views"; the `≥` vs `>` distinction is intentional).

    Field derivations are identical to records_from_parse either way: one record per
    leg, priced at spot only (no P&L, no usd_entry_amount)."""
    mapping = DEFAULT_BOOK_MAPPING if book_mapping is None else book_mapping
    sql = _DB_SQL_EXPOSURE if for_exposure else _DB_SQL_GRID
    by_trade: Dict[str, list] = {}
    for r in conn.execute(sql, {"as_of": as_of_date}).fetchall():
        by_trade.setdefault(r[0], []).append(r)
    records: List[dict] = []
    unresolved: List[Unresolved] = []
    for trade_id, legs in by_trade.items():
        product, pair, desc, trade_date, price, strategy, account, is_ndf = legs[0][1:9]
        if product == "FX_OPTION":
            continue  # delta records built by option_records_from_db below, not from the notional leg
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
    opt_records, opt_unresolved = option_records_from_db(conn, as_of_date, mapping)
    return records + opt_records, unresolved + opt_unresolved


# FX options (2026-09-17): the option's delta joins the ladder, so Net/Gross, the
# per-currency risk table and the stress block include it (docs/BUILD_PLAN.md section
# 7, CLAUDE.md "Aggregate delta per currency"). Same reads as engine/ladder/ladder.py's
# delta_per_ccy: trades_official, official DELTA (per unit of trades.quantity, sign of
# the trade carried by quantity) and the PAIR's official SPOT joined on
# base_ccy || quote_ccy -- never on the option's own instrument_id, which is the blotter
# Symbol and can never match a SPOT row. Expiry-day options are excluded in both modes
# (settle_date > as_of): an option expiring today carries no delta by close, and its
# leg is not cash either (settles_cash = 0), so the grid has nothing to show for it.
_DB_SQL_OPTIONS = """
SELECT t.trade_id, t.instrument_id, t.description, t.trade_date, t.price, t.strategy, t.account,
       t.quantity, i.base_ccy, i.quote_ccy, i.is_ndf, l.settle_date,
       m.value AS delta, s.value AS spot
FROM trades_official t JOIN instruments i USING (instrument_id)
JOIN trade_legs l ON l.trade_id = t.trade_id AND l.leg_no = 1
LEFT JOIN marks_official m ON m.instrument_id = t.instrument_id AND m.mark_type = 'DELTA'
       AND m.as_of_date = :as_of
LEFT JOIN marks_official s ON s.instrument_id = i.base_ccy || i.quote_ccy AND s.mark_type = 'SPOT'
       AND s.as_of_date = :as_of AND s.settle_date = :as_of
WHERE t.product = 'FX_OPTION' AND t.trade_date <= :as_of AND l.settle_date > :as_of
ORDER BY t.trade_id, m.snapped_at DESC, s.snapped_at DESC
"""


def option_records_from_db(conn, as_of_date: str,
                           book_mapping: Dict[str, str] | None = None) -> Tuple[List[dict], List[Unresolved]]:
    """Two delta records per open FX option (base-ccy delta = quantity x DELTA at the
    option's expiry date; quote-ccy delta = -quantity x DELTA x pair SPOT), in the same
    shape as the FX leg records so build_exposure treats them like any other leg. An
    option with no official DELTA mark, or with a DELTA but no official SPOT for its
    pair, contributes nothing and is listed in `unresolved` with the reason -- never
    dropped silently, never valued at a substitute (CLAUDE.md: the engine must raise
    rather than drop a NULL delta; here the ladder shows it as unresolved instead)."""
    mapping = DEFAULT_BOOK_MAPPING if book_mapping is None else book_mapping
    records: List[dict] = []
    unresolved: List[Unresolved] = []
    seen: set = set()
    for row in conn.execute(_DB_SQL_OPTIONS, {"as_of": as_of_date}).fetchall():
        (trade_id, instrument_id, desc, trade_date, price, strategy, account,
         quantity, base_ccy, quote_ccy, is_ndf, expiry, delta, spot) = row
        if trade_id in seen:
            continue  # latest official mark per trade wins (ORDER BY snapped_at DESC)
        seen.add(trade_id)
        pair = f"{base_ccy}{quote_ccy}"
        if delta is None:
            unresolved.append(Unresolved(trade_id, instrument_id,
                                         f"no official DELTA mark for {instrument_id} on {as_of_date}"))
            continue
        if spot is None:
            unresolved.append(Unresolved(trade_id, instrument_id,
                                         f"no official SPOT for {pair} on {as_of_date} (option delta not converted)"))
            continue
        common = {
            "trade_id": trade_id, "source_row_id": trade_id, "product_type": "FX_OPTION",
            "symbol": f"{pair}-{trade_id}", "symbol_description": desc,
            "trade_date": trade_date, "settlement_date": expiry, "currency_pair": pair,
            "entry_rate": float(price), "book_source": strategy, "book": mapping.get(strategy, strategy),
            "account": account, "fund": FUND, "strategy": strategy,
            "is_ndf": int(is_ndf), "settles_cash": 0,
        }
        records.append({**common, "currency": base_ccy, "local_amount": float(quantity) * float(delta)})
        records.append({**common, "currency": quote_ccy,
                        "local_amount": -float(quantity) * float(delta) * float(spot)})
    return records, unresolved


def exposure_records_from_db(conn, as_of_date: str,
                             book_mapping: Dict[str, str] | None = None) -> Tuple[List[dict], List[Unresolved]]:
    """records_from_db(..., for_exposure=True) under an explicit name, so callers that
    only want delta/exposure aggregation (Net USD, Gross USD, per-currency delta -- i.e.
    anything feeding engine.ladder.exposure.build_exposure / portfolio_totals /
    summary) cannot accidentally pick up the grid's `>=` rule. The Cash ladder grid
    itself must keep calling records_from_db(..., for_exposure=False) (the default)."""
    return records_from_db(conn, as_of_date, book_mapping, for_exposure=True)
