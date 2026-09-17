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
"""
from __future__ import annotations

import datetime as dt
import sqlite3
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
               i.base_ccy, i.quote_ccy, l.settle_date
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
        WHERE t.product = 'FUTURE' AND t.trade_date <= :as_of AND l.leg_no = 1
    """


def _irs_sql(theme: bool) -> str:
    theme_col = "COALESCE(t.theme, '')" if theme else "''"
    return f"""
        SELECT t.trade_id, t.instrument_id, t.product, t.strategy, {theme_col} AS theme,
               t.trade_date, t.quantity, t.price AS fill, i.base_ccy, l.settle_date
        FROM trades_official t JOIN instruments i USING (instrument_id) JOIN trade_legs l USING (trade_id)
        WHERE t.product = 'IRS' AND t.trade_date <= :as_of AND l.leg_no = 1
    """


def _opt_sql(theme: bool) -> str:
    theme_col = "COALESCE(t.theme, '')" if theme else "''"
    return f"""
        SELECT t.trade_id, t.instrument_id, t.product, t.strategy, {theme_col} AS theme,
               t.trade_date, t.quantity, t.price AS fill, i.base_ccy, i.quote_ccy, l.settle_date
        FROM trades_official t JOIN instruments i USING (instrument_id) JOIN trade_legs l USING (trade_id)
        WHERE t.product = 'FX_OPTION' AND t.trade_date <= :as_of AND l.leg_no = 1
    """


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
    hit = _mark_at(conn, inv_pair, as_of, "SPOT", as_of)
    if hit is not None and hit[0]:
        out = (1.0 / float(hit[0]), inv_pair, hit[1])
    else:
        direct_pair = f"{quote_ccy}USD"
        hit = _mark_at(conn, direct_pair, as_of, "SPOT", as_of)
        out = (float(hit[0]), direct_pair, hit[1]) if hit is not None else (_NAN, None, None)
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
        self.usd_memo, self.last_memo = {}, {}

    def execute(self, *args, **kwargs):
        return self.conn.execute(*args, **kwargs)


def value_book(conn: sqlite3.Connection, as_of: str) -> pd.DataFrame:
    """One row per trade at `as_of`'s marks. See module docstring and BUILD_PLAN section 2.
    Every mark this function reads comes from `marks_official`, always."""
    rows = []
    theme = _has_theme_column(conn)
    holidays = load_holidays()
    # sqlite3 params: positional IN(...) placeholders followed by :as_of.
    fx = pd.read_sql_query(_fx_sql(theme), conn, params=(*FX_PRODUCTS, as_of))
    fut = pd.read_sql_query(_fut_sql(theme), conn, params={"as_of": as_of})
    irs = pd.read_sql_query(_irs_sql(theme), conn, params={"as_of": as_of})
    opt = pd.read_sql_query(_opt_sql(theme), conn, params={"as_of": as_of})
    conn = _BookConn(conn, as_of)  # one load of the day's marks and realised rows for every row below

    for r in fx.itertuples(index=False):
        status = "SETTLED" if r.settle_date < as_of else "OPEN"
        reported_product = _reported_product(r.product, r.trade_date, r.settle_date, holidays)
        base = {
            "trade_id": r.trade_id, "instrument_id": r.instrument_id, "product": reported_product,
            "strategy": r.strategy, "theme": r.theme, "trade_date": r.trade_date,
            "settle_date": r.settle_date, "status": status, "quantity": r.quantity, "fill": r.fill,
        }
        if status == "SETTLED":
            rows.append({**base, **_settled_fx_row(conn, r, as_of)})
            continue
        rows.append({**base, **_open_fx_row(conn, r, as_of)})

    for r in fut.itertuples(index=False):
        status = "SETTLED" if r.settle_date < as_of else "OPEN"
        base = {
            "trade_id": r.trade_id, "instrument_id": r.instrument_id, "product": r.product,
            "strategy": r.strategy, "theme": r.theme, "trade_date": r.trade_date,
            "settle_date": r.settle_date, "status": status, "quantity": r.quantity, "fill": r.fill,
        }
        if status == "SETTLED":
            rows.append({**base, **_settled_future_row(conn, r, as_of)})
            continue
        rows.append({**base, **_open_future_row(conn, r, as_of)})

    for r in irs.itertuples(index=False):
        status = "SETTLED" if r.settle_date < as_of else "OPEN"
        base = {
            "trade_id": r.trade_id, "instrument_id": r.instrument_id, "product": r.product,
            "strategy": r.strategy, "theme": r.theme, "trade_date": r.trade_date,
            "settle_date": r.settle_date, "status": status, "quantity": r.quantity, "fill": r.fill,
        }
        if status == "SETTLED":
            rows.append({**base, **_settled_irs_row(conn, r, as_of)})
            continue
        rows.append({**base, **_open_irs_row(conn, r, as_of)})

    for r in opt.itertuples(index=False):
        status = "SETTLED" if r.settle_date < as_of else "OPEN"
        base = {
            "trade_id": r.trade_id, "instrument_id": r.instrument_id, "product": r.product,
            "strategy": r.strategy, "theme": r.theme, "trade_date": r.trade_date,
            "settle_date": r.settle_date, "status": status, "quantity": r.quantity, "fill": r.fill,
        }
        if status == "SETTLED":
            rows.append({**base, **_settled_option_row(conn, r, as_of)})
            continue
        rows.append({**base, **_open_option_row(conn, r, as_of)})

    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    return pd.DataFrame(rows, columns=COLUMNS)


def _open_fx_row(conn, r, as_of) -> dict:
    out = dict(mark=_NAN, mark_date=r.settle_date, mark_source="", spot=_NAN, spot_source="",
               pnl_local=_NAN, pnl_usd=_NAN, pnl_spot_usd=_NAN, pnl_carry_usd=_NAN, reason="", note="")
    m_hit = _mark_at(conn, r.instrument_id, r.settle_date, "FWD_OUTRIGHT", as_of)
    if m_hit is None:
        out["reason"] = f"no FWD_OUTRIGHT mark for {r.instrument_id} settle {r.settle_date} on {as_of}"
        return out
    m, m_src = float(m_hit[0]), m_hit[1]
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
    spot_hit = _mark_at(conn, r.instrument_id, as_of, "SPOT", as_of)
    if spot_hit is None:
        out["pnl_spot_usd"], out["pnl_carry_usd"] = pnl_usd, 0.0
        out["note"] = f"no SPOT for {r.instrument_id} on {as_of}; carry split unavailable"
        return out
    m_spot = float(spot_hit[0])
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
    return None if row is None else (float(row[0]), row[1], row[2])


def _frozen_row(conn, r) -> Optional[dict]:
    """A settled trade that has no `realised_pnl` row yet, valued exactly as
    `engine.pnl.ledger.realise_settled` would freeze it -- the last official mark on or
    before its settlement date -- but without writing anything (value_book is read-only
    and runs on read-only connections). Returns None when that mark is not on file, so the
    caller reports Unavailable. Added 2026-09-17: before this, realise_settled was only
    ever called from the Bloomberg feed, so on any PC without a live session every
    settled trade stayed Unavailable forever and took the headline LTD with it, even with
    every official mark loaded. The arithmetic here and in realise_settled must stay
    identical; realise_settled remains the path that persists the frozen figure."""
    product, settle = r.product, r.settle_date
    if product in FX_PRODUCTS:
        hit = _last_official_on_or_before(conn, r.instrument_id, "SPOT", settle)
        if hit is None:
            return None
        m, m_day, m_src = hit
        s, s_pair, s_src = usd_per_quote(conn, r.quote_ccy, m_day)
        if s != s or s_pair is None:
            return None
        pnl_local = r.quantity * (m - r.fill)
        pnl_usd = pnl_local * s
        spot, spot_src, mark_label = s, s_src, "spot"
    elif product == "FUTURE":
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
        pnl_local = pnl_usd = pv + float(cf[0])
        spot, spot_src, mark_label = 1.0, "identity", "PV + cashflows"
    elif product == "FX_OPTION":
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
    when = "" if m_day == settle else f" dated {m_day} (last before settlement)"
    return dict(mark=m, mark_date=m_day, mark_source=m_src, spot=spot, spot_source=spot_src,
                pnl_local=pnl_local, pnl_usd=pnl_usd, pnl_spot_usd=pnl_usd, pnl_carry_usd=0.0, reason="",
                note=f"frozen at {mark_label}{when}; not yet recorded in realised_pnl")


def _provisional(conn, r, as_of) -> dict:
    """A settled trade with no frozen result yet: valued at the last official mark on
    or before settlement (`_frozen_row`, the same figure `realise_settled` will
    persist), Unavailable when that mark is not on file. The realised table is never
    written here; `realise_settled` does that properly later. A settled trade with no
    frozen result and no official mark on or before its settlement is simply
    Unavailable, never a provisional value from a fallback source (2026-09-17 user
    decision, "no bnp fall back" -- see module docstring)."""
    trade_id = r.trade_id
    frozen = _frozen_row(conn, r)
    if frozen is not None:
        return frozen
    return dict(mark=_NAN, mark_date="", mark_source="", spot=_NAN, spot_source="",
                pnl_local=_NAN, pnl_usd=_NAN, pnl_spot_usd=_NAN, pnl_carry_usd=_NAN,
                reason=f"settled trade {trade_id}: no official mark on or before its settlement "
                       f"{r.settle_date}, so it cannot be frozen")


def _settled_fx_row(conn, r, as_of) -> dict:
    trade_id = r.trade_id
    row = _realised_row(conn, trade_id)
    if row is None:
        return _provisional(conn, r, as_of)
    return dict(mark=_NAN, mark_date=row["spot_as_of_date"], mark_source=row["spot_source"],
                spot=row["spot_usd_per_local"], spot_source=row["spot_source"],
                pnl_local=_NAN, pnl_usd=float(row["pnl_usd"]), pnl_spot_usd=float(row["pnl_usd"]),
                pnl_carry_usd=0.0, reason="", note=row["note"])


def _open_future_row(conn, r, as_of) -> dict:
    out = dict(mark=_NAN, mark_date=r.settle_date, mark_source="", spot=1.0, spot_source="identity",
               pnl_local=_NAN, pnl_usd=_NAN, pnl_spot_usd=_NAN, pnl_carry_usd=0.0, reason="", note="")
    m_hit = _mark_at(conn, r.instrument_id, r.settle_date, "FUTURE_PX", as_of)
    if m_hit is None:
        out["reason"] = f"no FUTURE_PX mark for {r.instrument_id} expiry {r.settle_date} on {as_of}"
        return out
    m, m_src = float(m_hit[0]), m_hit[1]
    out["mark"], out["mark_source"] = m, m_src
    pnl = r.quantity * r.multiplier * (m - r.fill)
    out["pnl_local"], out["pnl_usd"], out["pnl_spot_usd"] = pnl, pnl, pnl
    return out


def _settled_future_row(conn, r, as_of) -> dict:
    trade_id = r.trade_id
    row = _realised_row(conn, trade_id)
    if row is None:
        return _provisional(conn, r, as_of)
    return dict(mark=_NAN, mark_date=row["spot_as_of_date"], mark_source=row["spot_source"],
                spot=1.0, spot_source="identity", pnl_local=_NAN, pnl_usd=float(row["pnl_usd"]),
                pnl_spot_usd=float(row["pnl_usd"]), pnl_carry_usd=0.0, reason="", note=row["note"])


# --------------------------------------------------------------------------- IRS
def _open_irs_row(conn, r, as_of) -> dict:
    """PV_USD + CASHFLOW_USD at the swap's maturity date on `as_of` (module docstring)."""
    out = dict(mark=_NAN, mark_date=r.settle_date, mark_source="", spot=1.0, spot_source="identity",
               pnl_local=_NAN, pnl_usd=_NAN, pnl_spot_usd=_NAN, pnl_carry_usd=0.0, reason="", note="")
    pv_hit = _mark_at(conn, r.instrument_id, r.settle_date, "PV_USD", as_of)
    if pv_hit is None:
        out["reason"] = f"no PV_USD mark for {r.instrument_id} maturity {r.settle_date} on {as_of}"
        return out
    cf_hit = _mark_at(conn, r.instrument_id, r.settle_date, "CASHFLOW_USD", as_of)
    if cf_hit is None:
        out["reason"] = f"no CASHFLOW_USD mark for {r.instrument_id} maturity {r.settle_date} on {as_of}"
        return out
    pv, pv_src = float(pv_hit[0]), pv_hit[1]
    pnl = pv + float(cf_hit[0])
    out["mark"], out["mark_source"] = pv, pv_src
    out["pnl_local"], out["pnl_usd"], out["pnl_spot_usd"] = pnl, pnl, pnl
    if float(cf_hit[0]) != 0.0:
        out["note"] = f"includes {float(cf_hit[0]):,.2f} USD of settled coupons"
    return out


def _settled_irs_row(conn, r, as_of) -> dict:
    row = _realised_row(conn, r.trade_id)
    if row is None:
        return _provisional(conn, r, as_of)
    return dict(mark=_NAN, mark_date=row["spot_as_of_date"], mark_source=row["spot_source"],
                spot=1.0, spot_source="identity", pnl_local=_NAN, pnl_usd=float(row["pnl_usd"]),
                pnl_spot_usd=float(row["pnl_usd"]), pnl_carry_usd=0.0, reason="", note=row["note"])


# --------------------------------------------------------------------------- FX options
def _open_option_row(conn, r, as_of) -> dict:
    """quantity * (PREMIUM - fill) in base currency, converted to USD at spot."""
    out = dict(mark=_NAN, mark_date=r.settle_date, mark_source="", spot=_NAN, spot_source="",
               pnl_local=_NAN, pnl_usd=_NAN, pnl_spot_usd=_NAN, pnl_carry_usd=0.0, reason="", note="")
    m_hit = _mark_at(conn, r.instrument_id, r.settle_date, "PREMIUM", as_of)
    if m_hit is None:
        out["reason"] = f"no PREMIUM mark for {r.instrument_id} expiry {r.settle_date} on {as_of}"
        return out
    m, m_src = float(m_hit[0]), m_hit[1]
    out["mark"], out["mark_source"] = m, m_src
    s, s_pair, s_src = usd_per_quote(conn, r.base_ccy, as_of)
    if s != s or s_pair is None:
        out["reason"] = f"no SPOT for USD conversion of {r.base_ccy} on {as_of}"
        return out
    out["spot"], out["spot_source"] = s, s_src
    pnl_local = r.quantity * (m - r.fill)
    pnl_usd = pnl_local * s
    out["pnl_local"], out["pnl_usd"], out["pnl_spot_usd"] = pnl_local, pnl_usd, pnl_usd
    return out


def _settled_option_row(conn, r, as_of) -> dict:
    row = _realised_row(conn, r.trade_id)
    if row is None:
        return _provisional(conn, r, as_of)
    return dict(mark=_NAN, mark_date=row["spot_as_of_date"], mark_source=row["spot_source"],
                spot=row["spot_usd_per_local"], spot_source=row["spot_source"],
                pnl_local=_NAN, pnl_usd=float(row["pnl_usd"]), pnl_spot_usd=float(row["pnl_usd"]),
                pnl_carry_usd=0.0, reason="", note=row["note"])
