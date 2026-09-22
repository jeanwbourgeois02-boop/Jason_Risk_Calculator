"""Layer 2 valuation: one row per trade, at one date's marks. docs/BUILD_PLAN.md section 2.

`value_book(conn, as_of)` is the headline calculation: FX (spot, forward, swap legs --
all stored as ordinary 2-leg FX trades, see the swap packaging note below) and futures,
each valued fresh from `marks_official`. A missing official mark gives NaN and a
`reason`; it is never substituted from anywhere else.

2026-09-17 user decision, "no bnp fall back - that excel and everything linked to it
need to go" (`docs/bnp-excel-removal.md`): this module used to accept a `marks_source`
parameter (on `value_book`, `usd_per_quote`, `_mark_at`, and threaded through every
row-builder helper) that could retry a row with no official mark against an explicit
non-official source (BNP_BVAL, i.e. reading `marks` directly). That fallback mechanism
was deleted 2026-09-17 morning, leaving `marks_source` accepted-and-ignored everywhere
so not-yet-updated callers would not crash; now that every caller across the repo has
been checked (none pass a non-default value -- see git history for the full audit),
the parameter itself is removed outright from every function in this module,
`engine/pnl/ledger.py`'s `ltd`/`period_pnl`/`period_pnl_by`, and the `_mark_at` call in
`engine/ladder/futures_delta.py`. Every lookup goes through `marks_official`,
unconditionally -- there is no longer even a vestigial parameter suggesting otherwise.

Settled trades are never recomputed: their row is read back from `realised_pnl`
(engine/pnl/ledger.realise_settled must have populated it first; a settled trade with
no realised_pnl row is Unavailable, not recomputed from a stale open-trade formula).

Swap packaging: per CLAUDE.md's package_id rule and docs/BUILD_PLAN.md task B, an
FX_SWAP is stored as TWO ordinary `trades` rows (near + far), each with its own 2 legs,
sharing `package_id`. So no special-casing is needed here: both legs value exactly like
any other FX_FWD row; grouping by package_id for display is a UI concern.

USD conversion `S` (quote currency -> USD) at spot: identity when quote is USD,
otherwise the SPOT mark of instrument `USD<quote>` inverted, or of `<quote>USD`
directly -- whichever is on file. No USD leg is ever invented for a cross.

IRS and FX_OPTION rows (2026-09-17, user decision "the Total book must be the whole
book"): the same one-row-per-trade shape, so the header, every period figure, the
Total book strip and the bundles all include them.
  - IRS: `pnl_usd = PV_USD + CASHFLOW_USD` from `marks_official` at the swap's maturity
    (`settle_date` of leg 1) on `as_of`, both written by `engine/rates`. A swap dealt at
    its fixed rate with no upfront is worth zero at the fill by construction, so this
    is the mark-minus-fill analogue of the FX formula (CLAUDE.md "P&L conventions");
    CASHFLOW_USD (net coupons already settled) keeps LTD continuous across a payment
    and at maturity. `mark` = PV_USD, `spot` = 1 (the pricer already converted at spot).
  - FX_OPTION: `pnl_local = quantity * (PREMIUM_mark - fill)` in the pair's base currency
    (PREMIUM marks and `trades.price` are both base-ccy fraction of base notional,
    docs/open-questions.md item 61d), `pnl_usd = pnl_local * S` with `S` = USD per base
    unit at spot (identity when base is USD).
  Missing marks give NaN with a reason exactly like FX. Matured swaps and expired
  options read their frozen row from `realised_pnl` (engine/pnl/ledger.realise_settled).
  - EQ_OPTION, a listed index option (user decision 2026-09-21: "Bloomberg's option
    price"): valued exactly like a future, `contracts * multiplier * (m - fill)`, with `m`
    Bloomberg's own price of the option (the official FUTURE_PX mark on the option's
    instrument at its expiry date, in index points like the fill). No model is involved.

One bad value, one trade (2026-09-18, guards only -- no formula changed): every stored
figure a formula uses (`trades.quantity`, `trades.price`, `instruments.multiplier`, each
`marks.value`, `realised_pnl.pnl_usd`) passes through `_number` first. SQLite keeps text
it cannot convert as TEXT even in a REAL column, and `float()` on one such cell used to
raise out of `value_book`, which blanked the header, every Blotter strip and every table
at once ("could not convert string to float: '<a date>'" on the Bloomberg PC: a
`realised_pnl.pnl_usd` holding a date, see `_readable_realised`). Now the trade that needs
the bad value is left unpriced with a `reason` naming the trade id, the table.column and
the offending value (`_guarded_row`), a settled trade whose frozen row is unreadable is
valued as not yet frozen (`_provisional`), and every other trade prices as before.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from collections import namedtuple
from typing import Optional

import pandas as pd

from engine.pnl.calendar import _is_business_day, load_holidays

COLUMNS = [
    "trade_id", "instrument_id", "product", "strategy", "theme", "trade_date",
    "settle_date", "status", "quantity", "fill", "mark", "mark_date", "mark_source",
    "spot", "spot_source", "pnl_local", "pnl_usd", "pnl_spot_usd", "pnl_carry_usd",
    "reason", "note",
]

FX_PRODUCTS = ("FX_SPOT", "FX_FWD", "FX_SWAP")

_NAN = float("nan")


class _BadValue(ValueError):
    """A stored figure that is not a number: TEXT sitting in a REAL column (SQLite keeps
    text it cannot convert as text, whatever the column's declared type). `where` names
    the table.column -- and for a mark, which mark -- so the reason on the trade's row can
    say exactly what to fix. A ValueError subclass, so a caller that never heard of it
    sees the same exception class `float()` raised before this guard existed."""

    def __init__(self, where: str, value):
        self.where, self.value = where, value
        super().__init__(f"{where} is not a number ({value!r})")


def _number(value, where: str) -> float:
    """`value` as a finite float, or `_BadValue` naming `where` and the offending value.
    Guard only (2026-09-18): every figure that reaches the P&L formulas passes through
    here first, so one bad cell leaves ONE trade unpriced with a reason instead of
    raising out of `value_book` and blanking the header, every Blotter strip and every
    table at once. Never substitutes anything: a value that is not a number is an error
    on that trade, not a zero."""
    if value is None or isinstance(value, (bool, bytes)):
        raise _BadValue(where, value)
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise _BadValue(where, value) from None
    if out != out or out in (float("inf"), float("-inf")):
        raise _BadValue(where, value)
    return out


def _mark_number(hit, instrument_id: str, settle_date: str, mark_type: str, as_of: str) -> float:
    """The value of a `_mark_at` hit as a float (`_BadValue` naming the mark otherwise)."""
    return _number(hit[0], f"marks.value ({mark_type} for {instrument_id} settle {settle_date} on {as_of})")


def _business_days_between(start: dt.date, end: dt.date, holidays, cap: Optional[int] = None) -> int:
    """Count business days strictly after `start` up to and including `end` (0 if
    `end <= start`). Used only to relabel a near-dated FX_FWD as "FX_SPOT" for display
    (user decision 2026-09-15, item 2); storage/product in `trades` is unchanged.

    `cap` (2026-09-17, complaint B perf): `_reported_product` only ever needs to know
    "<=2 or not" -- the exact count for a many-month-dated forward was previously
    walked day by day regardless (profiled at ~150k `_is_business_day` calls / 0.3s for
    a 772-row book, the single largest cost in `value_book` after the SQL itself), so
    once the running count exceeds `cap` this returns immediately with whatever count
    it has reached so far (not the true total -- callers that pass `cap` must only ever
    compare the result against values `<= cap`, never rely on the exact figure)."""
    if end <= start:
        return 0
    count = 0
    d = start
    while d < end:
        d += dt.timedelta(days=1)
        if _is_business_day(d, holidays):
            count += 1
            if cap is not None and count > cap:
                return count
    return count


def _reported_product(product: str, trade_date: str, settle_date: str, holidays) -> str:
    """user decision 2026-09-15, item 2: an FX_FWD trade whose settle_date is at most 2
    business days after trade_date is reported as "FX_SPOT" (a same/next/T+2 day forward
    is economically a spot trade). Only the reported `product` column changes; the
    stored `trades.product` value is never touched."""
    if product != "FX_FWD":
        return product
    d0 = dt.date.fromisoformat(trade_date)
    d1 = dt.date.fromisoformat(settle_date)
    if _business_days_between(d0, d1, holidays, cap=2) <= 2:
        return "FX_SPOT"
    return product


def _has_theme_column(conn: sqlite3.Connection) -> bool:
    return any(r[1] == "theme" for r in conn.execute("PRAGMA table_info(trades)"))


def _fx_sql(theme: bool) -> str:
    theme_col = "COALESCE(t.theme, '')" if theme else "''"
    return f"""
        SELECT t.trade_id, t.instrument_id, t.product, t.strategy, {theme_col} AS theme,
               t.trade_date, t.quantity, t.price AS fill,
               i.base_ccy, i.quote_ccy, i.is_ndf, l.settle_date
        FROM trades_official t JOIN instruments i USING (instrument_id) JOIN trade_legs l USING (trade_id)
        WHERE t.product IN ({",".join("?" * len(FX_PRODUCTS))}) AND t.trade_date <= ?
          AND l.leg_no = 1
    """


def _fut_sql(theme: bool) -> str:
    theme_col = "COALESCE(t.theme, '')" if theme else "''"
    return f"""
        SELECT t.trade_id, t.instrument_id, t.product, t.strategy, {theme_col} AS theme,
               t.trade_date, t.quantity, t.price AS fill, i.multiplier, l.settle_date
        FROM trades_official t JOIN instruments i USING (instrument_id) JOIN trade_legs l USING (trade_id)
        WHERE t.product IN ('FUTURE', 'EQ_OPTION') AND t.trade_date <= :as_of AND l.leg_no = 1
    """


def _irs_sql(theme: bool) -> str:
    theme_col = "COALESCE(t.theme, '')" if theme else "''"
    return f"""
        SELECT t.trade_id, t.instrument_id, t.product, t.strategy, {theme_col} AS theme,
               t.trade_date, t.quantity, t.price AS fill, i.base_ccy, l.settle_date
        FROM trades_official t JOIN instruments i USING (instrument_id) JOIN trade_legs l USING (trade_id)
        WHERE t.product = 'IRS' AND t.trade_date <= :as_of AND l.leg_no = 1
    """


def _opt_sql(theme: bool, terms: bool = True) -> str:
    """`terms`: the option's own terms from `instrument_options`, which `closed_out_options`
    matches on (False on a database that has no such table yet: nothing is closed out there)."""
    theme_col = "COALESCE(t.theme, '')" if theme else "''"
    terms_cols = ("COALESCE(o.strike, 0) AS strike, COALESCE(o.option_type, '') AS option_type, "
                  "COALESCE(o.payoff, 'VANILLA') AS payoff, COALESCE(o.barrier_level, 0) AS barrier_level, "
                  "COALESCE(o.avg_start_date, '9999-12-31') AS avg_start_date") if terms else (
                  "0 AS strike, '' AS option_type, 'VANILLA' AS payoff, 0 AS barrier_level, "
                  "'9999-12-31' AS avg_start_date")
    terms_join = "LEFT JOIN instrument_options o ON o.instrument_id = t.instrument_id" if terms else ""
    return f"""
        SELECT t.trade_id, t.instrument_id, t.product, t.strategy, {theme_col} AS theme,
               t.trade_date, t.quantity, t.price AS fill, i.base_ccy, i.quote_ccy, l.settle_date,
               {terms_cols}
        FROM trades_official t JOIN instruments i USING (instrument_id) JOIN trade_legs l USING (trade_id)
        {terms_join}
        WHERE t.product = 'FX_OPTION' AND t.trade_date <= :as_of AND l.leg_no = 1
    """


def _has_option_terms(conn: sqlite3.Connection) -> bool:
    """Every term `_opt_sql` reads is a column of `instrument_options` (a stale database can
    lack the table, or `payoff` and the columns added after it)."""
    columns = {r[1] for r in conn.execute("PRAGMA table_info(instrument_options)")}
    return {"strike", "option_type", "payoff", "barrier_level", "avg_start_date"} <= columns


def _mark_at(conn: sqlite3.Connection, instrument_id: str, settle_date: str, mark_type: str,
             as_of: str) -> Optional[tuple]:
    """(value, source) of the *official* mark for `settle_date`, dated exactly `as_of`,
    or None. Always reads `marks_official`; there is no non-official fallback (2026-09-17
    user decision, "no bnp fall back" -- see module docstring)."""
    cache = getattr(conn, "official_marks", None)
    if cache is not None and conn.as_of == as_of:
        return cache.get((instrument_id, settle_date, mark_type))
    row = conn.execute(
        "SELECT value, source, snapped_at FROM marks_official WHERE instrument_id = :i "
        "AND settle_date = :s AND mark_type = :m AND as_of_date = :d ORDER BY snapped_at DESC LIMIT 1",
        {"i": instrument_id, "s": settle_date, "m": mark_type, "d": as_of},
    ).fetchone()
    return None if row is None else (row[0], row[1])




# --------------------------------------------------------------------- near marks
# User decision 2026-09-22 ("For the ndf or spot or irs forward curves, if there is no
# price, we should always interpolate/extrapolate with near marks"): a mark that is not on
# file for the exact key is worked out from the official marks nearest to it, on the fly,
# and nothing is written to `marks` (the Market data tab still shows the gap). The row's
# mark source names what was done, so no figure is ever a silent substitute. Two steps:
#   1. a FWD_OUTRIGHT only: along the day's own curve, linear in date between the two
#      pillars either side of the leg's date (the pair's SPOT at the spot date and every
#      official outright of the pair on that day, any date); before the first pillar the
#      first (spot), beyond the last a straight line through the last two, or the last
#      alone when there is only one;
#   2. any mark: in time, between the same mark on the nearest earlier close and the
#      nearest later close, linear in calendar days; with a neighbour on one side only,
#      that one is taken as it stands. CASHFLOW_USD (coupons settled so far, a step
#      function) takes the nearest earlier close, the later one only with none earlier.
# `_mark_at` itself stays the exact lookup (engine/ladder/futures_delta.py wants exactly
# that: the ladder's delta is never filled); the row builders and the USD conversion read
# `_mark_near`. A pillar or neighbour whose stored value is not a number raises `_BadValue`
# like any other mark, so the trade is reported, never priced off a guess.
INTERP = "INTERP"


def _mark_near(conn, instrument_id: str, settle_date: str, mark_type: str, as_of: str) -> Optional[tuple]:
    """(value, source) of the official mark for the key on `as_of`, or the nearest-marks
    estimate described above, or None when there is nothing to work from."""
    hit = _mark_at(conn, instrument_id, settle_date, mark_type, as_of)
    if hit is not None:
        return hit
    memo = getattr(conn, "near_memo", None)
    key = (instrument_id, settle_date, mark_type, as_of)
    if memo is not None and key in memo:
        return memo[key]
    out = None
    if mark_type == "FWD_OUTRIGHT":
        out = _curve_interp(conn, instrument_id, settle_date, as_of)
    if out is None:
        out = _time_interp(conn, instrument_id, settle_date, mark_type, as_of)
    if memo is not None:
        memo[key] = out
    return out


def _day_pillars(conn, pair: str, as_of: str) -> list:
    """[(settle_date, value, source)] of the pair's official curve on `as_of`: its SPOT at
    the spot date (as_of + 2 weekdays, engine.ladder.usd_marks.spot_date) and every
    FWD_OUTRIGHT row of the pair dated `as_of`, sorted by date, one per date."""
    from engine.ladder.usd_marks import spot_date
    cache = getattr(conn, "official_marks", None)
    if cache is not None and conn.as_of == as_of:
        rows = [(s, v, src) for (i, s, mt), (v, src) in cache.items() if i == pair and mt == "FWD_OUTRIGHT"]
    else:
        rows = conn.execute(
            "SELECT settle_date, value, source FROM marks_official WHERE instrument_id = :i "
            "AND mark_type = 'FWD_OUTRIGHT' AND as_of_date = :d ORDER BY settle_date, snapped_at",
            {"i": pair, "d": as_of}).fetchall()
    by_date = {str(s): (v, src) for s, v, src in rows}
    spot = _mark_at(conn, pair, as_of, "SPOT", as_of)
    if spot is not None:
        by_date.setdefault(spot_date(as_of), (spot[0], spot[1]))
    return [(d, _number(v, f"marks.value (curve pillar {pair} {d} on {as_of})"), src)
            for d, (v, src) in sorted(by_date.items())]


def _curve_interp(conn, pair: str, settle_date: str, as_of: str) -> Optional[tuple]:
    pillars = _day_pillars(conn, pair, as_of)
    if not pillars:
        return None
    try:
        target = dt.date.fromisoformat(str(settle_date))
        dates = [dt.date.fromisoformat(d) for d, _, _ in pillars]
    except ValueError:
        return None
    if target <= dates[0]:
        d0, v0, _ = pillars[0]
        return v0, f"{INTERP}: {pair} {d0} mark of {as_of} (nearest, no earlier pillar)"
    if target >= dates[-1]:
        if len(pillars) == 1:
            d0, v0, _ = pillars[0]
            return v0, f"{INTERP}: {pair} {d0} mark of {as_of} (the only pillar)"
        (d0, v0, _), (d1, v1, _) = pillars[-2], pillars[-1]
        w = (target - dates[-2]).days / (dates[-1] - dates[-2]).days
        return v0 + w * (v1 - v0), f"{INTERP}: extrapolated from {pair} {d0} and {d1} marks of {as_of}"
    for k in range(1, len(pillars)):
        if target < dates[k]:
            (d0, v0, _), (d1, v1, _) = pillars[k - 1], pillars[k]
            w = (target - dates[k - 1]).days / (dates[k] - dates[k - 1]).days
            return v0 + w * (v1 - v0), f"{INTERP}: between {pair} {d0} and {d1} marks of {as_of}"
    return None


def _neighbour(conn, instrument_id: str, settle_date: str, mark_type: str, as_of: str, later: bool):
    """(value, as_of_date, source) of the same mark on the nearest close after (`later`) or
    before `as_of`, or None. A SPOT / NDF_1M row is keyed on its own day (settle_date =
    as_of_date), every other mark on its fixed settle_date."""
    own_day = mark_type in ("SPOT", "NDF_1M")
    row = conn.execute(
        "SELECT value, as_of_date, source FROM marks_official WHERE instrument_id = :i AND mark_type = :m "
        + ("AND settle_date = as_of_date " if own_day else "AND settle_date = :s ")
        + ("AND as_of_date > :d ORDER BY as_of_date ASC, snapped_at DESC LIMIT 1" if later else
           "AND as_of_date < :d ORDER BY as_of_date DESC, snapped_at DESC LIMIT 1"),
        {"i": instrument_id, "m": mark_type, "s": settle_date, "d": as_of}).fetchone()
    if row is None:
        return None
    return (_number(row[0], f"marks.value ({mark_type} for {instrument_id} on {row[1]})"), str(row[1]), row[2])


def _time_interp(conn, instrument_id: str, settle_date: str, mark_type: str, as_of: str) -> Optional[tuple]:
    before = _neighbour(conn, instrument_id, settle_date, mark_type, as_of, later=False)
    after = _neighbour(conn, instrument_id, settle_date, mark_type, as_of, later=True)
    if before is None and after is None:
        return None
    if before is not None and after is not None and mark_type != "CASHFLOW_USD":
        v0, d0, _ = before
        v1, d1, _ = after
        try:
            day, day0, day1 = (dt.date.fromisoformat(x) for x in (as_of, d0, d1))
            w = (day - day0).days / (day1 - day0).days
        except (ValueError, ZeroDivisionError):
            return v0, f"{INTERP}: {mark_type} of {d0} (nearest earlier close)"
        return v0 + w * (v1 - v0), f"{INTERP}: {mark_type} between the {d0} and {d1} closes"
    hit = before if before is not None else after
    when = "nearest earlier close" if before is not None else "nearest later close, none earlier"
    return hit[0], f"{INTERP}: {mark_type} of {hit[1]} ({when})"


def usd_per_quote(conn: sqlite3.Connection, quote_ccy: str, as_of: str) -> tuple:
    """(S, pair, mark_source) converting 1 unit of quote_ccy to USD at spot on `as_of`.
    Never invents a USD leg for a cross: tries USD<quote> (inverted) then <quote>USD."""
    if quote_ccy == "USD":
        return 1.0, "USD", "identity"
    memo = getattr(conn, "usd_memo", None)
    key = (quote_ccy, as_of)
    if memo is not None and key in memo:
        return memo[key]
    inv_pair = f"USD{quote_ccy}"
    hit = _mark_near(conn, inv_pair, as_of, "SPOT", as_of)
    if hit is not None and hit[0]:
        out = (1.0 / _mark_number(hit, inv_pair, as_of, "SPOT", as_of), inv_pair, hit[1])
    else:
        direct_pair = f"{quote_ccy}USD"
        hit = _mark_near(conn, direct_pair, as_of, "SPOT", as_of)
        out = ((_mark_number(hit, direct_pair, as_of, "SPOT", as_of), direct_pair, hit[1])
               if hit is not None else (_NAN, None, None))
    if memo is not None:
        memo[key] = out
    return out


def _realised_row(conn: sqlite3.Connection, trade_id: str) -> Optional[pd.Series]:
    realised = getattr(conn, "realised", None)
    if realised is not None:
        return realised.get(trade_id)
    df = pd.read_sql_query("SELECT * FROM realised_pnl WHERE trade_id = :t", conn, params={"t": trade_id})
    return None if df.empty else df.iloc[0]


class _BookConn:
    """The connection value_book hands to its row builders: the real connection plus
    everything they would otherwise fetch with one query per trade, loaded once per
    call (2026-09-17: 772 trades x 5 header dates took 4 s; now well under 1 s -- see
    also the 2026-09-17 "no bnp fall back" change, which deleted a *second* one-query-
    per-row path this class used to preload for; see git history / this module's
    docstring if that preload is ever needed again).
      official_marks -- every marks_official row dated `as_of`, keyed
                        (instrument_id, settle_date, mark_type) -> (value, source),
                        newest snapped_at winning, exactly what _mark_at returns
      realised       -- realised_pnl rows keyed by trade_id
      usd_memo       -- usd_per_quote results for this call
      last_memo      -- _last_official_on_or_before results for this call
    `execute` delegates, so helpers that need an ad-hoc query (_frozen_row's historical
    look-back) still work unchanged."""

    def __init__(self, conn: sqlite3.Connection, as_of: str):
        self.conn, self.as_of = conn, as_of
        marks = {}
        for inst, settle, mt, value, source in conn.execute(
                "SELECT instrument_id, settle_date, mark_type, value, source FROM marks_official "
                "WHERE as_of_date = ? ORDER BY snapped_at", (as_of,)):
            marks[(inst, settle, mt)] = (value, source)
        self.official_marks = marks

        df = pd.read_sql_query("SELECT * FROM realised_pnl", conn)
        self.realised = {row["trade_id"]: row for _, row in df.iterrows()} if not df.empty else {}
        self.usd_memo, self.last_memo, self.near_memo = {}, {}, {}
        self.closed_out = {}   # trade_id -> CloseOut, set by value_book (`closed_out_from_rows`)

    def execute(self, *args, **kwargs):
        return self.conn.execute(*args, **kwargs)


def value_book(conn: sqlite3.Connection, as_of: str, trade_ids=None) -> pd.DataFrame:
    """One row per trade at `as_of`'s marks. See module docstring and BUILD_PLAN section 2.
    Every mark this function reads comes from `marks_official`, always.

    `trade_ids` (2026-09-21) values only those trades, each row exactly as the whole book
    would give it (a closed-out option group is still recognised over every option on
    file): the fill (`engine.pnl.reference.fill_book`) asks an earlier day for the handful
    of trades with no price, not for the whole book again."""
    rows = []
    theme = _has_theme_column(conn)
    holidays = load_holidays()
    # sqlite3 params: positional IN(...) placeholders followed by :as_of.
    fx = pd.read_sql_query(_fx_sql(theme), conn, params=(*FX_PRODUCTS, as_of))
    fut = pd.read_sql_query(_fut_sql(theme), conn, params={"as_of": as_of})
    irs = pd.read_sql_query(_irs_sql(theme), conn, params={"as_of": as_of})
    opt = pd.read_sql_query(_opt_sql(theme, _has_option_terms(conn)), conn, params={"as_of": as_of})
    conn = _BookConn(conn, as_of)  # one load of the day's marks and realised rows for every row below
    conn.closed_out = closed_out_from_rows(opt)
    if trade_ids is not None:
        wanted = set(trade_ids)
        fx, fut, irs, opt = (f[f["trade_id"].isin(wanted)] for f in (fx, fut, irs, opt))

    for r in fx.itertuples(index=False):
        rows.append(_guarded_row(conn, r, as_of, _open_fx_row, _settled_fx_row, _TRADE_NUMBERS, holidays))
    for r in fut.itertuples(index=False):
        rows.append(_guarded_row(conn, r, as_of, _open_future_row, _settled_future_row, _FUTURE_NUMBERS))
    for r in irs.itertuples(index=False):
        rows.append(_guarded_row(conn, r, as_of, _open_irs_row, _settled_irs_row, _IRS_NUMBERS))
    for r in opt.itertuples(index=False):
        rows.append(_guarded_row(conn, r, as_of, _open_option_row, _settled_option_row, _TRADE_NUMBERS))

    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    return pd.DataFrame(rows, columns=COLUMNS)


# (attribute on the SQL row, table.column it came from, whether the row's P&L formula
# uses it): the stored figures `_guarded_row` checks before any formula sees them. A swap's
# P&L is PV_USD + CASHFLOW_USD, both marks, so its quantity and fixed rate are shown on
# the row but never enter the P&L: a bad one blanks that cell and is named in `note`,
# while the swap still prices.
_TRADE_NUMBERS = (("quantity", "trades.quantity", True), ("fill", "trades.price", True))
_FUTURE_NUMBERS = _TRADE_NUMBERS + (("multiplier", "instruments.multiplier", True),)
_IRS_NUMBERS = (("quantity", "trades.quantity", False), ("fill", "trades.price", False))


def _unpriced(reason: str) -> dict:
    return dict(mark=_NAN, mark_date="", mark_source="", spot=_NAN, spot_source="", pnl_local=_NAN,
                pnl_usd=_NAN, pnl_spot_usd=_NAN, pnl_carry_usd=_NAN, reason=reason, note="")


def _guarded_row(conn, r, as_of, open_builder, settled_builder, numbers, holidays=None) -> dict:
    """One `value_book` row for the trade `r`: exactly what the loops in `value_book`
    built inline before 2026-09-18 (status, the FX_SPOT relabel when `holidays` is given,
    then the settled or the open builder), with one guard around it.

    Why (user, Bloomberg PC, 2026-09-18: every Blotter view showed "could not convert
    string to float: '<a date>'" and no headline or strip had a figure): ONE stored value
    that is not a number -- there, a `realised_pnl.pnl_usd` holding a date, written by
    `engine.pnl.ledger`'s then-positional INSERT into a table whose column order depends
    on the database's age -- raised out of `value_book`, and since the header, every
    strip and every table start from `value_book`, one bad cell blanked the whole app.
    Now the trade whose figure is bad is left UNPRICED (NaN P&L, like a trade with no
    mark) with a `reason` naming the trade id, the table.column and the offending value,
    and every other trade prices as before. Nothing is substituted: a bad `quantity` /
    `fill` shows as missing (NaN) on the row, never as 0 and never as the raw text, so
    the views' own number formatting cannot trip over it either. No formula changes."""
    base = {
        "trade_id": r.trade_id, "instrument_id": r.instrument_id, "product": r.product,
        "strategy": r.strategy, "theme": r.theme, "trade_date": r.trade_date,
        "settle_date": r.settle_date, "status": "", "quantity": _NAN, "fill": _NAN,
    }
    bad, shown_only = None, []
    for attr, column, in_formula in numbers:
        try:
            _number(getattr(r, attr), column)
        except _BadValue as exc:
            if in_formula:
                bad = bad or exc
            else:
                shown_only.append(str(exc))
        else:
            if attr in base:
                base[attr] = getattr(r, attr)
    try:
        base["status"] = "SETTLED" if r.settle_date < as_of else "OPEN"
        if base["status"] == "OPEN" and r.trade_id in getattr(conn, "closed_out", {}):
            base["status"] = "CLOSED"   # an option bought and sold back in full: not live (`_closed_option_row`)
        if holidays is not None:
            base["product"] = _reported_product(r.product, r.trade_date, r.settle_date, holidays)
        if bad is not None:
            raise bad
        builder = settled_builder if base["status"] == "SETTLED" else open_builder
        row = {**base, **builder(conn, r, as_of)}
        if shown_only:
            note = row.get("note") or ""
            row["note"] = "; ".join(([note] if isinstance(note, str) and note else []) + shown_only)
        return row
    except _BadValue as exc:
        return {**base, **_unpriced(f"trade {r.trade_id}: {exc}")}
    except (TypeError, ValueError, ArithmeticError) as exc:
        return {**base, **_unpriced(f"trade {r.trade_id}: could not be valued ({type(exc).__name__}: {exc})")}


def ndf_fixing(r) -> str:
    """The fixing date of an NDF ticket (value date less 2 business days, `ndf.fixing_date`,
    the Ladder's own rule), or '' for any other ticket (`engine.ladder.ndf.is_ndf_pair`)."""
    from engine.ladder.ndf import fixing_date, is_ndf_pair
    if not is_ndf_pair(str(r.instrument_id or ""), int(getattr(r, "is_ndf", 0) or 0)):
        return ""
    return fixing_date(r.settle_date)


def _ndf_fixed_on(r, as_of: str) -> str:
    """The fixing date when this open FX ticket is an NDF that has fixed on or before
    `as_of`, else ''. From its fixing an NDF is done (user, 2026-09-22: "NDFs - once they
    expire, they should disappear ... 0 delta and 0 carry, they just disappears as they
    expired", reversing the 2026-09-21 "like settled cash" rule): its P&L is `Q x (S_fix - f)`
    at the pair's official SPOT of the fixing date, converted at that date's spot, and does
    not move again; the ledger freezes it at the same spot after the value date
    (`engine.pnl.ledger`). Before the fixing it is marked at its value date's forward like
    any leg (with none on file, the near-marks rule: along the day's curve, spot alone being
    spot -- which is what the 2026-09-21/22 NDF and metal "no forward" cases now are)."""
    fixed_on = ndf_fixing(r)
    return fixed_on if fixed_on and fixed_on <= as_of else ""


def _open_fx_row(conn, r, as_of) -> dict:
    out = dict(mark=_NAN, mark_date=r.settle_date, mark_source="", spot=_NAN, spot_source="",
               pnl_local=_NAN, pnl_usd=_NAN, pnl_spot_usd=_NAN, pnl_carry_usd=_NAN, reason="", note="")
    fixed_on = _ndf_fixed_on(r, as_of)
    if fixed_on:
        # Fixed: the spot of the fixing date, and the conversion of that date, frozen.
        m_hit = _mark_near(conn, r.instrument_id, fixed_on, "SPOT", fixed_on)
        out["mark_date"] = fixed_on
        if m_hit is None:
            out["reason"] = f"no SPOT mark for {r.instrument_id} on {fixed_on} (NDF fixed that day)"
            return out
        m, m_src = _mark_number(m_hit, r.instrument_id, fixed_on, "SPOT", fixed_on), m_hit[1]
        s, s_pair, s_src = usd_per_quote(conn, r.quote_ccy, fixed_on)
        if s != s or s_pair is None:
            out["mark"], out["mark_source"] = m, m_src
            out["reason"] = f"no SPOT for USD conversion of {r.quote_ccy} on {fixed_on} (NDF fixed that day)"
            return out
        pnl_usd = r.quantity * (m - r.fill) * s
        out.update(mark=m, mark_source=m_src, spot=s, spot_source=s_src, pnl_local=r.quantity * (m - r.fill),
                   pnl_usd=pnl_usd, pnl_spot_usd=pnl_usd, pnl_carry_usd=0.0,
                   note=f"NDF fixed {fixed_on}: frozen at the spot of {fixed_on}, no delta, no carry")
        return out
    m_hit = _mark_near(conn, r.instrument_id, r.settle_date, "FWD_OUTRIGHT", as_of)
    if m_hit is None:
        out["reason"] = f"no FWD_OUTRIGHT mark for {r.instrument_id} settle {r.settle_date} on {as_of}"
        return out
    m, m_src = _mark_number(m_hit, r.instrument_id, r.settle_date, "FWD_OUTRIGHT", as_of), m_hit[1]
    out["mark"], out["mark_source"] = m, m_src
    s, s_pair, s_src = usd_per_quote(conn, r.quote_ccy, as_of)
    if s != s or s_pair is None:
        out["spot_source"] = ""
        out["reason"] = f"no SPOT for USD conversion of {r.quote_ccy} on {as_of}"
        return out
    out["spot"], out["spot_source"] = s, s_src
    pnl_local = r.quantity * (m - r.fill)
    pnl_usd = pnl_local * s
    out["pnl_local"], out["pnl_usd"] = pnl_local, pnl_usd
    spot_hit = _mark_near(conn, r.instrument_id, as_of, "SPOT", as_of)
    if spot_hit is None:
        out["pnl_spot_usd"], out["pnl_carry_usd"] = pnl_usd, 0.0
        out["note"] = f"no SPOT for {r.instrument_id} on {as_of}; carry split unavailable"
        return out
    try:
        m_spot = _mark_number(spot_hit, r.instrument_id, as_of, "SPOT", as_of)
    except _BadValue as exc:
        # The pair's own SPOT only splits the P&L into spot and carry; the P&L itself
        # (outright mark, converted at the quote currency's spot) is already computed.
        out["pnl_spot_usd"], out["pnl_carry_usd"] = pnl_usd, 0.0
        out["note"] = f"{exc}; carry split unavailable"
        return out
    pnl_carry = r.quantity * (m - m_spot) * s
    out["pnl_carry_usd"] = pnl_carry
    out["pnl_spot_usd"] = pnl_usd - pnl_carry
    return out


def _last_official_on_or_before(conn, instrument_id: str, mark_type: str, day: str):
    """(value, as_of_date, source) of the last official mark on or before `day`, or None.
    Same lookup `engine.pnl.ledger._last_on_or_before` uses to freeze a trade."""
    memo = getattr(conn, "last_memo", None)
    key = (instrument_id, mark_type, day)
    if memo is not None and key in memo:
        return memo[key]
    out = _last_official_query(conn, instrument_id, mark_type, day)
    if memo is not None:
        memo[key] = out
    return out


def _last_official_query(conn, instrument_id: str, mark_type: str, day: str):
    row = conn.execute(
        "SELECT value, as_of_date, source FROM marks_official WHERE instrument_id = :i "
        "AND mark_type = :m AND as_of_date <= :d ORDER BY as_of_date DESC, snapped_at DESC LIMIT 1",
        {"i": instrument_id, "m": mark_type, "d": day}).fetchone()
    if row is None:
        return None
    # A mark that is not a number is an error on the trade that needs it (`_guarded_row`
    # reports it), never a reason to reach further back for an older mark.
    return (_number(row[0], f"marks.value ({mark_type} for {instrument_id} on {row[1]})"), row[1], row[2])


# A settled NDF with no past fix on file (user decision 2026-09-21: "we can use a present spot
# for the past fixes"). The one exception to "missing stays missing": an NDF ticket whose pair
# has NO official SPOT on or before its settlement is valued at the latest official SPOT on
# file on or before the valuation date, and the row says which date's spot that is. NDF pairs
# only (engine.ladder.ndf.is_ndf_pair); a deliverable trade in the same position stays blank.
# The ledger persists it only once the backfill has tried for the settlement date's own close
# (`engine.pnl.ledger.realise_settled`, `ndf_present_spot`), and drops it for the strict freeze
# as soon as that close is on file (`ledger.purge_superseded_present_spot`).
PRESENT_SPOT_NOTE = "present spot: none on file on or before settlement"


def present_spot_for_ndf(conn, pair: str, as_of: str) -> Optional[tuple]:
    """(value, as_of_date, source) of the latest official SPOT of the NDF `pair` on or before
    `as_of`; None when the pair is not an NDF or has no SPOT on file at all. Called only after
    the on-or-before-settlement lookup found nothing, so the row is dated after settlement."""
    from engine.ladder.ndf import is_ndf_pair
    row = conn.execute("SELECT is_ndf FROM instruments WHERE instrument_id = ?", (pair,)).fetchone()
    if not is_ndf_pair(pair, int((row[0] if row else 0) or 0)):
        return None
    return _last_official_on_or_before(conn, pair, "SPOT", as_of)


def _frozen_row(conn, r, as_of: Optional[str] = None) -> Optional[dict]:
    """A settled trade that has no `realised_pnl` row yet, valued exactly as
    `engine.pnl.ledger.realise_settled` would freeze it -- the last official mark on or
    before its settlement date -- but without writing anything (value_book is read-only
    and runs on read-only connections). Returns None when that mark is not on file, so the
    caller reports Unavailable. Added 2026-09-17: before this, realise_settled was only
    ever called from the Bloomberg feed, so on any PC without a live session every
    settled trade stayed Unavailable forever and took the headline LTD with it, even with
    every official mark loaded. The arithmetic here and in realise_settled must stay
    identical; realise_settled remains the path that persists the frozen figure.
    `as_of`: the valuation date, for `present_spot_for_ndf` (an NDF with no past fix on file)."""
    product, settle = r.product, r.settle_date
    present = False
    if product in FX_PRODUCTS:
        hit = _last_official_on_or_before(conn, r.instrument_id, "SPOT", settle)
        if hit is None and as_of is not None:
            hit, present = present_spot_for_ndf(conn, r.instrument_id, as_of), True
        if hit is None:
            return None
        m, m_day, m_src = hit
        s, s_pair, s_src = usd_per_quote(conn, r.quote_ccy, m_day)
        if s != s or s_pair is None:
            return None
        pnl_local = r.quantity * (m - r.fill)
        pnl_usd = pnl_local * s
        spot, spot_src, mark_label = s, s_src, "spot"
    elif product in ("FUTURE", "EQ_OPTION"):
        hit = _last_official_on_or_before(conn, r.instrument_id, "FUTURE_PX", settle)
        if hit is None:
            return None
        m, m_day, m_src = hit
        pnl_local = pnl_usd = r.quantity * r.multiplier * (m - r.fill)
        spot, spot_src, mark_label = 1.0, "identity", "settlement price"
    elif product == "IRS":
        hit = _last_official_on_or_before(conn, r.instrument_id, "PV_USD", settle)
        if hit is None:
            return None
        pv, m_day, m_src = hit
        cf = conn.execute(
            "SELECT value FROM marks_official WHERE instrument_id = :i AND mark_type = 'CASHFLOW_USD' "
            "AND as_of_date = :d ORDER BY snapped_at DESC LIMIT 1", {"i": r.instrument_id, "d": m_day}).fetchone()
        if cf is None:
            return None
        m = pv
        pnl_local = pnl_usd = pv + _number(cf[0], f"marks.value (CASHFLOW_USD for {r.instrument_id} on {m_day})")
        spot, spot_src, mark_label = 1.0, "identity", "PV + cashflows"
    elif product == "FX_OPTION":
        closed = getattr(conn, "closed_out", {}).get(r.trade_id)
        if closed is not None:   # bought and sold back in full: the closing fill, never a PREMIUM mark
            return _closed_frozen_row(conn, r, closed)
        hit = _last_official_on_or_before(conn, r.instrument_id, "PREMIUM", settle)
        if hit is None:
            return None
        m, m_day, m_src = hit
        s, s_pair, s_src = usd_per_quote(conn, r.base_ccy, m_day)
        if s != s or s_pair is None:
            return None
        pnl_local = r.quantity * (m - r.fill)
        pnl_usd = pnl_local * s
        spot, spot_src, mark_label = s, s_src, "premium"
    else:
        return None
    when = "" if m_day == settle else f" dated {m_day} ({PRESENT_SPOT_NOTE if present else 'last before settlement'})"
    return dict(mark=m, mark_date=m_day, mark_source=m_src, spot=spot, spot_source=spot_src,
                pnl_local=pnl_local, pnl_usd=pnl_usd, pnl_spot_usd=pnl_usd, pnl_carry_usd=0.0, reason="",
                note=f"frozen at {mark_label}{when}; not yet recorded in realised_pnl")


def _provisional(conn, r, as_of, unreadable: str = "") -> dict:
    """A settled trade with no frozen result yet: valued at the last official mark on
    or before settlement (`_frozen_row`, the same figure `realise_settled` will
    persist), Unavailable when that mark is not on file. The realised table is never
    written here; `realise_settled` does that properly later. A settled trade with no
    frozen result and no official mark on or before its settlement is simply
    Unavailable, never a provisional value from a fallback source (2026-09-17 user
    decision, "no bnp fall back" -- see module docstring).

    `unreadable` (2026-09-18): the trade HAS a `realised_pnl` row but a figure in it is
    not a number (`_readable_realised`). Such a row is no frozen result at all, so the
    trade is valued exactly as if it had none -- same `_frozen_row`, same arithmetic --
    and the note/reason names the unreadable value; `engine.pnl.ledger.realise_settled`
    deletes and re-freezes such rows the next time it runs."""
    trade_id = r.trade_id
    frozen = _frozen_row(conn, r, as_of)
    if frozen is not None:
        if unreadable:
            frozen["note"] = frozen["note"].replace(
                "not yet recorded in realised_pnl",
                f"its stored realised_pnl row is unreadable ({unreadable}) and is rebuilt by the next Bloomberg pull")
        return frozen
    if unreadable:
        return _unpriced(f"settled trade {trade_id}: its realised_pnl row is unreadable ({unreadable}) and there "
                         f"is no official mark on or before its settlement {r.settle_date}, so it cannot be frozen again")
    return dict(mark=_NAN, mark_date="", mark_source="", spot=_NAN, spot_source="",
                pnl_local=_NAN, pnl_usd=_NAN, pnl_spot_usd=_NAN, pnl_carry_usd=_NAN,
                reason=f"settled trade {trade_id}: no official mark on or before its settlement "
                       f"{r.settle_date}, so it cannot be frozen")


def _readable_realised(conn, trade_id: str, with_spot: bool = False) -> tuple:
    """(row, pnl_usd, unreadable) for the trade's frozen `realised_pnl` row: `(None, None,
    "")` when none is on file, `(row, pnl_usd, "")` when its figures are numbers, and
    `(None, None, why)` when the row is on file but a figure in it is not a number --
    the caller then values the trade as not yet frozen (`_provisional`), never from the
    unreadable row and never as zero.

    How such a row came to exist (root cause of the 2026-09-18 Bloomberg-PC blank
    Blotter): `realised_pnl` gained `product` and `mark_type` on 2026-09-15. A fresh
    database has them in the middle of the table (the DDL order); a database created
    before that got them APPENDED by `ALTER TABLE ADD COLUMN`. The ledger's INSERT was
    positional, so on an older database every value landed two columns off and `pnl_usd`
    received `spot_as_of_date` -- a date string, which SQLite keeps as text in a REAL
    column. `float('2026-07-24')` then raised for every view. The INSERT names its
    columns now and `realise_settled` re-freezes such rows; this is the read-side guard."""
    row = _realised_row(conn, trade_id)
    if row is None:
        return None, None, ""
    try:
        pnl = _number(row["pnl_usd"], "realised_pnl.pnl_usd")
        if with_spot:
            _number(row["spot_usd_per_local"], "realised_pnl.spot_usd_per_local")
    except _BadValue as exc:
        return None, None, str(exc)
    return row, pnl, ""


def _settled_fx_row(conn, r, as_of) -> dict:
    row, pnl, unreadable = _readable_realised(conn, r.trade_id, with_spot=True)
    if row is None:
        return _provisional(conn, r, as_of, unreadable)
    return dict(mark=_NAN, mark_date=row["spot_as_of_date"], mark_source=row["spot_source"],
                spot=row["spot_usd_per_local"], spot_source=row["spot_source"],
                pnl_local=_NAN, pnl_usd=pnl, pnl_spot_usd=pnl,
                pnl_carry_usd=0.0, reason="", note=row["note"])


def _open_future_row(conn, r, as_of) -> dict:
    out = dict(mark=_NAN, mark_date=r.settle_date, mark_source="", spot=1.0, spot_source="identity",
               pnl_local=_NAN, pnl_usd=_NAN, pnl_spot_usd=_NAN, pnl_carry_usd=0.0, reason="", note="")
    m_hit = _mark_near(conn, r.instrument_id, r.settle_date, "FUTURE_PX", as_of)
    if m_hit is None:
        out["reason"] = (f"no Bloomberg price for the listed option {r.instrument_id} on {as_of}"
                         if r.product == "EQ_OPTION" else
                         f"no FUTURE_PX mark for {r.instrument_id} expiry {r.settle_date} on {as_of}")
        return out
    m, m_src = _mark_number(m_hit, r.instrument_id, r.settle_date, "FUTURE_PX", as_of), m_hit[1]
    out["mark"], out["mark_source"] = m, m_src
    pnl = r.quantity * r.multiplier * (m - r.fill)
    out["pnl_local"], out["pnl_usd"], out["pnl_spot_usd"] = pnl, pnl, pnl
    return out


def _settled_future_row(conn, r, as_of) -> dict:
    row, pnl, unreadable = _readable_realised(conn, r.trade_id)
    if row is None:
        return _provisional(conn, r, as_of, unreadable)
    return dict(mark=_NAN, mark_date=row["spot_as_of_date"], mark_source=row["spot_source"],
                spot=1.0, spot_source="identity", pnl_local=_NAN, pnl_usd=pnl,
                pnl_spot_usd=pnl, pnl_carry_usd=0.0, reason="", note=row["note"])


# --------------------------------------------------------------------------- IRS
def _open_irs_row(conn, r, as_of) -> dict:
    """PV_USD + CASHFLOW_USD at the swap's maturity date on `as_of` (module docstring)."""
    out = dict(mark=_NAN, mark_date=r.settle_date, mark_source="", spot=1.0, spot_source="identity",
               pnl_local=_NAN, pnl_usd=_NAN, pnl_spot_usd=_NAN, pnl_carry_usd=0.0, reason="", note="")
    pv_hit = _mark_near(conn, r.instrument_id, r.settle_date, "PV_USD", as_of)
    if pv_hit is None:
        out["reason"] = f"no PV_USD mark for {r.instrument_id} maturity {r.settle_date} on {as_of}"
        return out
    cf_hit = _mark_near(conn, r.instrument_id, r.settle_date, "CASHFLOW_USD", as_of)
    if cf_hit is None:
        out["reason"] = f"no CASHFLOW_USD mark for {r.instrument_id} maturity {r.settle_date} on {as_of}"
        return out
    pv, pv_src = _mark_number(pv_hit, r.instrument_id, r.settle_date, "PV_USD", as_of), pv_hit[1]
    cf = _mark_number(cf_hit, r.instrument_id, r.settle_date, "CASHFLOW_USD", as_of)
    pnl = pv + cf
    out["mark"], out["mark_source"] = pv, pv_src
    out["pnl_local"], out["pnl_usd"], out["pnl_spot_usd"] = pnl, pnl, pnl
    if cf != 0.0:
        out["note"] = f"includes {cf:,.2f} USD of settled coupons"
    return out


def _settled_irs_row(conn, r, as_of) -> dict:
    row, pnl, unreadable = _readable_realised(conn, r.trade_id)
    if row is None:
        return _provisional(conn, r, as_of, unreadable)
    return dict(mark=_NAN, mark_date=row["spot_as_of_date"], mark_source=row["spot_source"],
                spot=1.0, spot_source="identity", pnl_local=_NAN, pnl_usd=pnl,
                pnl_spot_usd=pnl, pnl_carry_usd=0.0, reason="", note=row["note"])


# --------------------------------------------------------------------------- FX options
METALS = frozenset({"XAU", "XAG", "XPT", "XPD"})


def option_fill_is_per_ounce(base_ccy: str, fill: float) -> bool:
    """A metal option's premium is dealt in the quote currency per ounce (USD 38.50 an
    ounce), not as a fraction of the base notional the way an FX option's is, while the
    PREMIUM mark is always that fraction. A fraction of notional can never reach 1, so a
    fill of 1 or more on a metal base is per-ounce and nothing else (user, 2026-09-21:
    "the gold options are wrong because it reads the ounces of gold as dollars"). Its
    start value is quantity x fill in the QUOTE currency; the current value is still
    quantity x PREMIUM x USD per ounce; P&L = current value - start value, in USD."""
    return base_ccy in METALS and fill == fill and abs(fill) >= 1.0


# Closed-out options (user, 2026-09-21: "for options closed out theyre not live ... for the pnl
# calculations this has to be factored in"). An option bought and sold back in full is not a
# live position: over trades of the SAME option (pair, call / put, payoff, strike, barrier,
# averaging start, expiry) whose quantities net to zero, the PREMIUM mark cancels out of
# sum(quantity x (PREMIUM - fill)) exactly, so the P&L is the fills' own difference and needs
# no mark. Each trade of such a group is valued at the closing fill instead of a PREMIUM mark:
# on the close-out date the total is what the marked formula gives, and it is still there when
# the marks are not. The conversion to USD is frozen as well (user, 2026-09-21: "of course you
# freeze the usd converstion"): the official spot of the close-out date -- the last trade's
# date -- or the last one before it, named in the note, so from the close-out on the figure
# never moves and nothing more is needed from Bloomberg for it; after expiry the ledger records
# that same figure in `realised_pnl`.
# The export books the buy and the sell-back under two instrument ids (the reference sample's
# EURSEK 23-Sep put: '...-197728105' bought, '...-197838147' sold), so the match is on the
# terms, never on the id; an option whose terms are not on file (strike 0 = not known) is
# never matched -- two unknown strikes could be a spread, not a close-out.
CLOSE_OUT_SOURCE = "CLOSE_OUT_FILL"
CloseOut = namedtuple("CloseOut", "price date trade_ids")


def _option_terms_key(r) -> Optional[tuple]:
    """What makes two option trades the same option, or None when the terms are not all on file."""
    payoff = (r.payoff or "VANILLA").upper()
    strike, barrier = _number(r.strike, "instrument_options.strike"), _number(r.barrier_level, "instrument_options.barrier_level")
    if payoff in ("ONE_TOUCH", "NO_TOUCH"):
        known = barrier > 0
    else:
        known = strike > 0 and r.option_type in ("CALL", "PUT") and (payoff not in ("BARRIER_KI", "BARRIER_KO") or barrier > 0)
    if not known:
        return None
    return (r.base_ccy, r.quote_ccy, r.settle_date, r.option_type, payoff, round(strike, 10), round(barrier, 10),
            r.avg_start_date)


def closed_out_from_rows(opt: pd.DataFrame) -> dict:
    """trade_id -> CloseOut(price, date, trade_ids) for every trade of a fully closed-out
    option among `opt` (the `_opt_sql` rows, already limited to trade_date <= as_of, so a past
    date sees the position as it stood then). `price` = the quantity-weighted fill of the
    closing side (the side opposite the first trade), `date` = the last trade's date. With one
    buy and one sell-back the opening trade carries quantity x (closing fill - fill) and the
    closing trade zero; with more fills the split between trades follows the average, and the
    total is the same whatever the split. A group with a quantity or fill that is not a number,
    or with gold fills dealt both per ounce and as a fraction, is left to the marked path."""
    groups: dict = {}
    for r in opt.itertuples(index=False):
        try:
            key = _option_terms_key(r)
            row = (r.trade_date, str(r.trade_id), _number(r.quantity, "trades.quantity"), _number(r.fill, "trades.price"),
                   option_fill_is_per_ounce(r.base_ccy, float(r.fill)))
        except (TypeError, ValueError):
            key, row = ("unreadable", r.base_ccy, r.quote_ccy, r.settle_date), None
        if key is not None:
            groups.setdefault(key, []).append(row)
    out = {}
    for rows in groups.values():
        if any(row is None for row in rows) or len({row[4] for row in rows}) != 1:
            continue
        rows.sort()
        gross = sum(abs(q) for _d, _t, q, _f, _oz in rows)
        if gross <= 0 or abs(sum(q for _d, _t, q, _f, _oz in rows)) > 1e-9 * gross:
            continue
        closing = [(q, f) for _d, _t, q, f, _oz in rows if q * rows[0][2] < 0]
        price = sum(abs(q) * f for q, f in closing) / sum(abs(q) for q, _f in closing)
        closed = CloseOut(price, max(d for d, *_rest in rows), tuple(t for _d, t, *_rest in rows))
        out.update({t: closed for t in closed.trade_ids})
    return out


def closed_out_options(conn: sqlite3.Connection, as_of: str) -> dict:
    """`closed_out_from_rows` for the option trades on file up to `as_of` (the ledger's entry point)."""
    opt = pd.read_sql_query(_opt_sql(False, _has_option_terms(conn)), conn, params={"as_of": as_of})
    return closed_out_from_rows(opt)


def close_out_ccy(base_ccy: str, quote_ccy: str, fill: float) -> str:
    """The currency a closed-out option's fills (and so its P&L) are in: the base currency, or
    the quote currency for a metal dealt per ounce (`option_fill_is_per_ounce`)."""
    return quote_ccy if option_fill_is_per_ounce(base_ccy, fill) else base_ccy


def last_usd_conversion(conn, ccy: str, day: str) -> Optional[tuple]:
    """(S, as_of_date, source): USD per unit of `ccy` at the last official SPOT on or before
    `day`, tried in `usd_per_quote`'s order; None when neither USD pair has one."""
    if ccy == "USD":
        return 1.0, day, "identity"
    hit = _last_official_on_or_before(conn, f"USD{ccy}", "SPOT", day)
    if hit is not None and hit[0]:
        return 1.0 / hit[0], hit[1], hit[2]
    return _last_official_on_or_before(conn, f"{ccy}USD", "SPOT", day)


def _close_out_note(closed: CloseOut) -> str:
    return (f"closed out {closed.date}: bought and sold back in full (trades {', '.join(closed.trade_ids)}); "
            f"realised at the closing fill {closed.price:.10g}, no PREMIUM mark needed")


def _closed_option_row(conn, r, closed: CloseOut) -> dict:
    """quantity * (closing fill - fill) in the fills' currency, converted to USD at the
    close-out date's spot (`last_usd_conversion`): the same figure on every date from the
    close-out on, before expiry and after it, and the one the ledger records after expiry."""
    out = dict(mark=closed.price, mark_date=closed.date, mark_source=CLOSE_OUT_SOURCE, spot=_NAN, spot_source="",
               pnl_local=_NAN, pnl_usd=_NAN, pnl_spot_usd=_NAN, pnl_carry_usd=0.0, reason="",
               note=_close_out_note(closed))
    ccy = close_out_ccy(r.base_ccy, r.quote_ccy, r.fill)
    hit = last_usd_conversion(conn, ccy, closed.date)
    if hit is None:
        out["reason"] = f"closed out {closed.date}; no official SPOT to convert {ccy} to USD on or before that date"
        return out
    s, s_day, s_src = hit
    out["spot"], out["spot_source"] = s, s_src
    if s_day != closed.date:
        out["note"] += f"; converted at spot dated {s_day} (last before the close-out)"
    pnl_local = r.quantity * (closed.price - r.fill)
    out["pnl_local"], out["pnl_usd"], out["pnl_spot_usd"] = pnl_local, pnl_local * s, pnl_local * s
    return out


def _closed_frozen_row(conn, r, closed: CloseOut) -> Optional[dict]:
    """A closed-out option past its expiry with no readable `realised_pnl` row: the figure
    `engine.pnl.ledger.realise_settled` will record; None when the close-out spot is not on file."""
    out = _closed_option_row(conn, r, closed)
    if out["reason"]:
        return None
    out["note"] += "; not yet recorded in realised_pnl"
    return out


def _open_option_row(conn, r, as_of) -> dict:
    """quantity * (PREMIUM - fill) in base currency, converted to USD at spot. A metal
    option dealt per ounce (`option_fill_is_per_ounce`): current value - start value.
    A closed-out option needs no PREMIUM mark (`_closed_option_row`)."""
    closed = getattr(conn, "closed_out", {}).get(r.trade_id)
    if closed is not None:
        return _closed_option_row(conn, r, closed)
    out = dict(mark=_NAN, mark_date=r.settle_date, mark_source="", spot=_NAN, spot_source="",
               pnl_local=_NAN, pnl_usd=_NAN, pnl_spot_usd=_NAN, pnl_carry_usd=0.0, reason="", note="")
    m_hit = _mark_near(conn, r.instrument_id, r.settle_date, "PREMIUM", as_of)
    if m_hit is None:
        out["reason"] = f"no PREMIUM mark for {r.instrument_id} expiry {r.settle_date} on {as_of}"
        return out
    m, m_src = _mark_number(m_hit, r.instrument_id, r.settle_date, "PREMIUM", as_of), m_hit[1]
    out["mark"], out["mark_source"] = m, m_src
    s, s_pair, s_src = usd_per_quote(conn, r.base_ccy, as_of)
    if s != s or s_pair is None:
        out["reason"] = f"no SPOT for USD conversion of {r.base_ccy} on {as_of}"
        return out
    out["spot"], out["spot_source"] = s, s_src
    if option_fill_is_per_ounce(r.base_ccy, r.fill):
        q, q_pair, _q_src = usd_per_quote(conn, r.quote_ccy, as_of)
        if q != q or q_pair is None:
            out["reason"] = f"no SPOT for USD conversion of {r.quote_ccy} on {as_of}"
            return out
        pnl_usd = r.quantity * m * s - r.quantity * r.fill * q      # current value - start value, USD
        pnl_local = pnl_usd / s
    else:
        pnl_local = r.quantity * (m - r.fill)
        pnl_usd = pnl_local * s
    out["pnl_local"], out["pnl_usd"], out["pnl_spot_usd"] = pnl_local, pnl_usd, pnl_usd
    return out


def _settled_option_row(conn, r, as_of) -> dict:
    row, pnl, unreadable = _readable_realised(conn, r.trade_id, with_spot=True)
    if row is None:
        closed = getattr(conn, "closed_out", {}).get(r.trade_id)
        if closed is not None and not unreadable:   # its own reason when the close-out spot is not on file
            return _closed_frozen_row(conn, r, closed) or _closed_option_row(conn, r, closed)
        return _provisional(conn, r, as_of, unreadable)
    return dict(mark=_NAN, mark_date=row["spot_as_of_date"], mark_source=row["spot_source"],
                spot=row["spot_usd_per_local"], spot_source=row["spot_source"],
                pnl_local=_NAN, pnl_usd=pnl, pnl_spot_usd=pnl,
                pnl_carry_usd=0.0, reason="", note=row["note"])
