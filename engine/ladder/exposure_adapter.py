"""Adapter: DB-recorded trades -> normalized records for
engine/ladder/exposure.build_exposure. Pure transformation over `trades_official` /
`trade_legs` / `instruments`; no marks, no UI.

Field mapping:
    trade_id          trades.trade_id
    trade_date        trades.trade_date
    settlement_date   trade_legs.settle_date
    currency_pair     trades.instrument_id
    local currency    the leg's own ccy (one record per leg, not per trade)
    local_amount      that leg's signed amount
    entry_rate        trade_legs.rate, carried for drill-down display only -- it plays
                      no part in any exposure.py computation (delta is always marked at
                      spot, never at the entry rate).
    book_source       trades.strategy
    book              book_mapping.get(book_source, book_source). Default mapping is identity,
                      so book == strategy; it is never silently renamed. Pass
                      book_mapping={'HAHY7': 'HA'} explicitly to reproduce the screenshot label.
    fund              constant 'NMMF'
    product_type      trades.product

One record per trade LEG, not per trade: engine/ladder/exposure.py is a pure delta table
(no P&L), so every leg, USD or not, is simply priced at spot -- a cross (e.g. EURSEK)
contributes one record per leg like any other trade, with no invented USD leg.

Excluded: CURRENCY balance rows (never reach `trades`), FUTURES (product != FX_*), and
any leg whose trade has no instrument record. Each exclusion is reported in
`unresolved`, never dropped silently.

Settled cash (user decision 2026-09-18, "there should be a settled cash row toward the
top" / "expired tickets must settle not disappear"): a ticket whose value date has
passed no longer vanishes from the ladder. Its legs land in ONE extra row, keyed by the
sentinel `settlement_date = SETTLED`, which the grid shows first as "Settled cash":
  - a deliverable leg (`settles_cash = 1`) is cash in its own currency from its value
    date on, and still carries that currency's delta (NOK received on a forward is NOK
    exposure until it is sold) -- so it is summed per (trade, currency) into the row;
  - a non-deliverable ticket (NDF leg pair, future, FX option -- `settles_cash = 0`)
    never delivers its local currency; the only cash it produces is its USD settlement,
    which is exactly the realised P&L `engine.pnl.ledger.realise_settled` froze for it
    (NDF: quantity x (fixing - fill) converted at that spot). That USD figure is read
    from `realised_pnl` -- never recomputed here -- and added to the row's USD column.
    A settled non-deliverable ticket with no realised row yet (no official mark on or
    before its value date) is listed in `unresolved` with a "settled ... USD settlement
    unknown" reason, never valued at a substitute.
Boundary: the grid keeps a leg settling exactly on as_of on its own date row (cash
that moves today), so its settled rule is `settle_date < as_of`; the exposure/delta
records use `settle_date <= as_of` for deliverable legs (by close it is cash, and cash
carries delta) and `> as_of` for open legs, so a deliverable leg is counted exactly
once either way. Non-deliverable USD settlements use `<` in both modes, matching the
ledger's own realisation rule (a settlement in transit carries no FX delta anyway).
The settled row only covers tickets the uploaded blotter carries: it is settled cash
from those tickets, not a bank balance.

NDF tickets are dated on their FIXING date (user decision 2026-09-21, "NDFs, show fixing
dates instead"; the rule and its one assumption live in engine/ladder/ndf.py): every leg
record of an NDF ticket carries `settlement_date` = fixing date (value date less 2
business days), with the real value date kept in `value_date` and the fixing repeated
in `fixing_date`. The Ladder tab's own rules read that date: the grid shows an NDF leg
while fixing date >= as_of, the tab's delta / exposure counts it while fixing date >
as_of. Once fixed an NDF is gone from the ladder altogether (user, 2026-09-22: "NDFs -
once they expire, they should disappear, not become setteld cash. I was wrong. 0 delta
and 0 carry, they just disappears as they expired", reversing the 2026-09-21 rule that
put a fixed NDF's legs in Settled cash): no leg on the grid, no delta, nothing in Settled
cash, neither its local legs nor its USD settlement, and nothing named under the grid for
it. engine/pnl freezes its P&L at the fixing date's spot (`engine.pnl.valuation`,
`engine.pnl.ledger`); the ladder shows nothing. Futures and FX options still bring their
realised USD settlement to Settled cash. "Is this an NDF ticket" is decided by
`ndf.is_ndf_pair`: the stored flag OR the pair's currencies against NDF_CCYS, so a
USDINR row stored as deliverable before INR joined the list is still treated as the NDF
it is (no deliverable INR cash in the settled row). engine/ladder/ladder.py's contract
SQL (delta_per_ccy, per_pair_delta) is untouched and stays value-date based.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Tuple

from engine.ladder.ndf import fixing_date, is_ndf_pair

FX_PRODUCTS = frozenset({"FX_SPOT", "FX_FWD", "FX_SWAP"})
FUND = "NMMF"
# Sentinel `settlement_date` of settled-cash records (module docstring). A plain word
# rather than a date so it can never collide with a real value date; it sorts after
# every ISO date in engine.ladder.exposure's pivot and the UI reorders it to the top.
SETTLED = "settled"


@dataclass(frozen=True)
class Unresolved:
    trade_id: str
    symbol: str
    reason: str


DEFAULT_BOOK_MAPPING: Dict[str, str] = {}  # identity: book == NM Strategy source value


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

# Settled deliverable legs, summed per (trade, currency) so a swap whose near and far
# legs have both settled in the same currency is one record (the exposure engine's
# natural key is (trade_id, currency, settlement_date), and both would share SETTLED).
# {op} is '<' (grid) or '<=' (exposure) -- a constant chosen in code, never user input.
_DB_SQL_SETTLED_LEGS = """
SELECT t.trade_id, t.product, t.instrument_id, t.description, t.trade_date, t.price,
       t.strategy, t.account, i.is_ndf,
       l.ccy, SUM(l.amount) AS amount, MAX(l.settle_date) AS settled_on
FROM trades_official t JOIN instruments i USING (instrument_id) JOIN trade_legs l USING (trade_id)
WHERE t.product IN ('FX_SPOT','FX_FWD','FX_SWAP') AND l.settles_cash = 1
  AND t.trade_date <= :as_of AND l.settle_date {op} :as_of
GROUP BY t.trade_id, l.ccy
ORDER BY t.trade_id, l.ccy
"""

# USD settlement of settled non-deliverable tickets: the realised P&L the ledger froze
# (engine/pnl/ledger.py), read back, never recomputed. NDF forwards plus futures and FX
# options; deliverable FX rows in realised_pnl are NOT read here -- their legs above
# already are the cash, adding their P&L too would double count. Which FX rows are NDF
# is decided in code by ndf.is_ndf_pair (stored flag OR the pair's currencies), not by
# `i.is_ndf` alone, so the query returns every FX row and the caller keeps the NDF ones.
_DB_SQL_SETTLED_REALISED = """
SELECT r.trade_id, t.product, t.instrument_id, t.description, t.trade_date, t.price,
       t.strategy, t.account, i.is_ndf, r.settle_date, r.pnl_usd
FROM realised_pnl r JOIN trades_official t USING (trade_id) JOIN instruments i USING (instrument_id)
WHERE t.trade_date <= :as_of AND r.settle_date < :as_of
  AND t.product IN ('FX_SPOT','FX_FWD','FX_SWAP','FUTURE','FX_OPTION')
ORDER BY r.trade_id
"""

# Settled non-deliverable tickets the ledger has NOT frozen yet: reported, never valued.
# Same NDF decision in code as above.
_DB_SQL_SETTLED_UNREALISED = """
SELECT t.trade_id, t.instrument_id, t.product, i.is_ndf, MAX(l.settle_date) AS settled_on
FROM trades_official t JOIN instruments i USING (instrument_id) JOIN trade_legs l USING (trade_id)
WHERE t.trade_date <= :as_of AND l.settle_date < :as_of
  AND t.product IN ('FX_SPOT','FX_FWD','FX_SWAP','FUTURE','FX_OPTION')
  AND t.trade_id NOT IN (SELECT trade_id FROM realised_pnl)
GROUP BY t.trade_id
ORDER BY t.trade_id
"""

# How an `Unresolved.reason` for an NDF that had fixed but not settled began, while such a
# ticket was named instead of valued (until 2026-09-21). Nothing writes it since: a fixed
# NDF is simply gone (module docstring); kept for ui/tabs/exposure.py, which still reads it.
NDF_FIXED_REASON_PREFIX = "settled at its fixing"


def _is_non_deliverable(product: str, pair: str, stored_flag) -> bool:
    """Futures and FX options never deliver; an FX ticket only when its pair is NDF."""
    return product not in FX_PRODUCTS or is_ndf_pair(pair, int(stored_flag or 0))


def _fixings(holidays=None):
    """value date -> fixing date, memoised for one read of the book."""
    if holidays is None:
        from engine.pnl.calendar import load_holidays
        holidays = load_holidays()
    cache: Dict[str, str] = {}

    def fix(value_date: str) -> str:
        if value_date not in cache:
            cache[value_date] = fixing_date(value_date, holidays)
        return cache[value_date]
    return fix


def _is_ndf_ticket(product: str, pair: str, stored_flag) -> bool:
    """An FX ticket on an NDF pair: gone from the ladder once fixed (module docstring)."""
    return product in FX_PRODUCTS and is_ndf_pair(pair, int(stored_flag or 0))


def settled_records_from_db(conn, as_of_date: str,
                            book_mapping: Dict[str, str] | None = None, *,
                            for_exposure: bool = False) -> Tuple[List[dict], List[Unresolved]]:
    """Settled-cash records (module docstring), same shape as the leg records plus
    `settled_on` (the value date the cash arrived, or the last one for a fully settled
    swap). `for_exposure=False`: deliverable legs with settle_date < as_of (the grid keeps
    today's on its own row); `for_exposure=True`: settle_date <= as_of (cash by close,
    still delta). Non-deliverable USD settlements are `< as_of` in both modes. A missing
    `realised_pnl` table (database older than the ledger) simply contributes nothing --
    the deliverable legs are still returned.

    NDF tickets (ndf.is_ndf_pair: stored flag OR the pair's currencies) never count as
    deliverable legs, whatever `settles_cash` an old database stored for them, and bring
    nothing here at all (module docstring: once fixed they disappear)."""
    mapping = DEFAULT_BOOK_MAPPING if book_mapping is None else book_mapping
    op = "<=" if for_exposure else "<"
    records: List[dict] = []
    unresolved: List[Unresolved] = []
    for r in conn.execute(_DB_SQL_SETTLED_LEGS.format(op=op), {"as_of": as_of_date}).fetchall():
        (trade_id, product, pair, desc, trade_date, price, strategy, account, is_ndf,
         ccy, amount, settled_on) = r
        if is_ndf_pair(pair, int(is_ndf or 0)):
            continue  # an NDF delivers no local currency: its cash is the USD settlement below
        records.append({
            "trade_id": trade_id, "source_row_id": trade_id, "product_type": product,
            "symbol": f"{pair}-{trade_id}", "symbol_description": desc,
            "trade_date": trade_date, "settlement_date": SETTLED, "settled_on": settled_on,
            "currency_pair": pair, "currency": ccy, "local_amount": float(amount),
            "entry_rate": float(price), "book_source": strategy, "book": mapping.get(strategy, strategy),
            "account": account, "fund": FUND, "strategy": strategy,
            "is_ndf": int(is_ndf), "settles_cash": 1,
        })
    try:
        realised = conn.execute(_DB_SQL_SETTLED_REALISED, {"as_of": as_of_date}).fetchall()
        unrealised = conn.execute(_DB_SQL_SETTLED_UNREALISED, {"as_of": as_of_date}).fetchall()
    except sqlite3.OperationalError:  # no realised_pnl table: ledger never created here
        return records, unresolved
    for r in realised:
        (trade_id, product, inst, desc, trade_date, price, strategy, account, is_ndf,
         settled_on, pnl_usd) = r
        if not _is_non_deliverable(product, inst, is_ndf) or _is_ndf_ticket(product, inst, is_ndf):
            continue  # deliverable FX: its legs above already are the cash; an NDF: gone once fixed
        records.append({
            "trade_id": trade_id, "source_row_id": trade_id, "product_type": product,
            "symbol": f"{inst}-{trade_id}", "symbol_description": desc,
            "trade_date": trade_date, "settlement_date": SETTLED, "settled_on": settled_on,
            "currency_pair": inst, "currency": "USD", "local_amount": float(pnl_usd),
            "entry_rate": float(price), "book_source": strategy, "book": mapping.get(strategy, strategy),
            "account": account, "fund": FUND, "strategy": strategy,
            "is_ndf": int(is_ndf), "settles_cash": 1,
        })
    for trade_id, inst, product, is_ndf, settled_on in unrealised:
        if not _is_non_deliverable(product, inst, is_ndf) or _is_ndf_ticket(product, inst, is_ndf):
            continue
        unresolved.append(Unresolved(
            trade_id, inst,
            f"settled {settled_on} ({product}), USD settlement unknown: not realised yet -- "
            f"no official mark on or before {settled_on}"))
    return records, unresolved


def records_from_db(conn, as_of_date: str,
                    book_mapping: Dict[str, str] | None = None, *,
                    for_exposure: bool = False,
                    include_settled: bool = True) -> Tuple[List[dict], List[Unresolved]]:
    """Same records as records_from_parse, read back from the SQLite tables the upload
    flow populates (trades / trade_legs / instruments). Read-only; no schema change.

    ``for_exposure=False`` (default): grid rule, trade_date <= as_of and
    settle_date >= as_of (matches the cash ladder grid's `>=` rule) -- use this for the
    Cash ladder tab display.

    ``for_exposure=True``: delta rule, trade_date <= as_of and settle_date > as_of for
    open legs. Use this for anything that feeds engine.ladder.exposure.build_exposure /
    portfolio_totals / summary (Net USD, Gross USD, per-currency delta): an NDF leg
    settling exactly on as_of carries no delta by close of that day, and a deliverable
    one is counted through the settled-cash records instead (below), never twice.

    ``include_settled`` (default True, 2026-09-18): append `settled_records_from_db`'s
    settled-cash records (module docstring) so expired tickets settle into the
    "Settled cash" row instead of disappearing. False restores the open-legs-only view.

    Field derivations are identical to records_from_parse either way: one record per
    leg, priced at spot only (no P&L, no usd_entry_amount).

    NDF tickets (2026-09-21, module docstring): each leg record is dated on its fixing
    date (`settlement_date` = `fixing_date`, the real value date in `value_date`), and
    the two rules above read that date -- grid: fixing date >= as_of; exposure: fixing
    date > as_of. The SQL's value-date filter is a superset (a fixing is never after its
    value date), so the fixing rule is applied here, leg by leg. Such a record always
    carries is_ndf = 1 and settles_cash = 0, whatever an old database stored. Every
    other leg record carries `value_date` = `settlement_date` and `fixing_date` = ''."""
    mapping = DEFAULT_BOOK_MAPPING if book_mapping is None else book_mapping
    sql = _DB_SQL_EXPOSURE if for_exposure else _DB_SQL_GRID
    by_trade: Dict[str, list] = {}
    for r in conn.execute(sql, {"as_of": as_of_date}).fetchall():
        by_trade.setdefault(r[0], []).append(r)
    records: List[dict] = []
    unresolved: List[Unresolved] = []
    fix = _fixings()
    for trade_id, legs in by_trade.items():
        product, pair, desc, trade_date, price, strategy, account, is_ndf = legs[0][1:9]
        if product == "FX_OPTION":
            continue  # delta records built by option_records_from_db below, not from the notional leg
        if product not in FX_PRODUCTS:
            unresolved.append(Unresolved(trade_id, pair, f"non-FX product {product} excluded"))
            continue
        ndf_ticket = is_ndf_pair(pair, int(is_ndf or 0))
        dated: Dict[tuple, dict] = {}  # NDF only: (ccy, fixing date) -> record
        for leg in legs:
            ccy, amount, settle_date, settles_cash = leg[9], leg[10], leg[11], leg[12]
            fixed_on = ""
            if ndf_ticket:
                fixed_on = fix(settle_date)
                if fixed_on < as_of_date or (for_exposure and fixed_on == as_of_date):
                    continue  # fixed: gone from the ladder (module docstring)
                if (ccy, fixed_on) in dated:
                    # Two value dates sharing one fixing date (a weekend value date): one
                    # record, or build_exposure's (trade, currency, date) key would clash.
                    dated[(ccy, fixed_on)]["local_amount"] += float(amount)
                    continue
            record = {
                "trade_id": trade_id, "source_row_id": trade_id, "product_type": product,
                "symbol": f"{pair}-{trade_id}", "symbol_description": desc,
                "trade_date": trade_date, "settlement_date": fixed_on or settle_date,
                "value_date": settle_date, "fixing_date": fixed_on, "currency_pair": pair,
                "currency": ccy, "local_amount": float(amount), "entry_rate": float(price),
                "book_source": strategy, "book": mapping.get(strategy, strategy),
                "account": account, "fund": FUND, "strategy": strategy,
                "is_ndf": 1 if ndf_ticket else int(is_ndf),
                "settles_cash": 0 if ndf_ticket else int(settles_cash),
            }
            if ndf_ticket:
                dated[(ccy, fixed_on)] = record
            records.append(record)
    opt_records, opt_unresolved = option_records_from_db(conn, as_of_date, mapping)
    records, unresolved = records + opt_records, unresolved + opt_unresolved
    if include_settled:
        settled, settled_unresolved = settled_records_from_db(
            conn, as_of_date, mapping, for_exposure=for_exposure)
        records, unresolved = records + settled, unresolved + settled_unresolved
    return records, unresolved


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
                             book_mapping: Dict[str, str] | None = None, *,
                             include_settled: bool = True) -> Tuple[List[dict], List[Unresolved]]:
    """records_from_db(..., for_exposure=True) under an explicit name, so callers that
    only want delta/exposure aggregation (Net USD, Gross USD, per-currency delta -- i.e.
    anything feeding engine.ladder.exposure.build_exposure / portfolio_totals /
    summary) cannot accidentally pick up the grid's `>=` rule. The Cash ladder grid
    itself must keep calling records_from_db(..., for_exposure=False) (the default)."""
    return records_from_db(conn, as_of_date, book_mapping, for_exposure=True,
                           include_settled=include_settled)
