"""Live Bloomberg feed for the FX cash ladder.

If `blpapi` is importable and a Bloomberg API service answers (Terminal / B-PIPE on
localhost:8194 by default), `LiveFeed` pulls every INTERVAL_SECONDS:
  * SPOT (PX_LAST, live ReferenceDataRequest) for every FX pair with an open leg;
  * FWD_OUTRIGHT for every open leg's own settle date (direct broken-date request first,
    standard-tenor interpolation as fallback, exactly as data/bloomberg/pull_marks.py
    does). There is no shared WORKDAY(as_of,5) maturity request any more: BUILD_PLAN.md
    section 2 marks each leg at the outright for its own settle_date only; the
    reconciliation view's single-maturity marks are entered manually on the Market data
    tab (see data/bloomberg/marks_csv.py::export_request, which still emits that request
    for the workbook comparison and is unchanged);
  * FUTURE_PX for every FUTURE instrument with an open leg, at the contract's own expiry
    (settle_date).
Rows are written to `marks` with INSERT OR REPLACE (same primary key each cycle, new
`snapped_at`), sources BBG_BFXFORWARD (official) / BBG_INTERP (fallback, never official).

If Bloomberg is NOT available nothing is written and nothing is invented: `rates_from_marks`
returns only what the marks table holds, and the status file says why the feed is down.

Every cycle writes a status JSON next to the database (`<db>.bloomberg_status.json`)
listing each requested (instrument, mark_type, settle_date) as OK or FAILED with the
value or the failure detail. `py -3 -m data.bloomberg.live --status` prints it;
`--once` runs a single pull.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import threading
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

INTERVAL_SECONDS = 120
STALE_AFTER_SECONDS = 600
SRC_SPOT_FWD = "BBG_BFXFORWARD"
SRC_INTERP = "BBG_INTERP"


# --------------------------------------------------------------------------- availability
def availability(host: str = "127.0.0.1", port: int = 8194, timeout: float = 0.5) -> Tuple[bool, str]:
    """(True, '') if blpapi imports and the API port accepts a TCP connection.

    The literal string "localhost" is resolved to 127.0.0.1 directly rather than left to
    getaddrinfo: on a PC without a Bloomberg Terminal, "localhost" resolves to both ::1
    and 127.0.0.1, and when nothing answers on ::1 (IPv6 loopback present but nothing
    bound there -- the common case) socket.create_connection tries it first and pays the
    full timeout before ever trying the working IPv4 address. Measured 2.07s for a single
    call at timeout=1.0 on a non-Bloomberg PC (2026-09-17); doubled again because
    data.bloomberg.backfill.start_auto_backfill runs its own separate availability() probe
    of the same host:port moments later inside the same create_app(start_feed=True) call
    (ui/app.py:246-259), for ~4.18s total before the app's first response. Callers that
    pass an explicit non-"localhost" host (a remote B-PIPE address, or an explicit
    "127.0.0.1"/"::1") are left untouched. timeout defaults to 0.5s (was an implicit 1.0s)
    since a loopback port either answers almost immediately or is not listening at all."""
    try:
        import blpapi  # noqa: F401
    except ImportError:
        return False, "blpapi is not installed on this computer"
    import socket
    connect_host = "127.0.0.1" if host == "localhost" else host
    try:
        with socket.create_connection((connect_host, port), timeout=timeout):
            return True, ""
    except OSError as exc:
        return False, f"no Bloomberg API service on {host}:{port} ({exc})"


# --------------------------------------------------------------------------- status file
def status_path(db_path) -> Path:
    p = Path(db_path)
    return p.with_name(p.name + ".bloomberg_status.json")


def write_status(db_path, status: dict) -> None:
    status_path(db_path).write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")


def read_status(db_path) -> Optional[dict]:
    p = status_path(db_path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def book_today() -> date:
    """The book date marks are stamped with: today in America/New_York (CLAUDE.md: the
    official close is 17:00 New York). Never the PC's local date -- a PC in Asia is a day
    ahead of New York until early afternoon, and marks stamped with its local date would
    be a day away from the date every screen looks up (found on the first live run,
    2026-09-17)."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York")).date()


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- requests
# trades_official (2026-09-17): legacy BNP-sourced trades never reach P&L or delta, so
# no mark is requested for them either -- the same view every engine query reads.
_OPEN_FX_SQL = """
SELECT DISTINCT i.instrument_id, i.bbg_ticker, l.settle_date
FROM trade_legs l JOIN trades_official t USING (trade_id) JOIN instruments i USING (instrument_id)
WHERE i.asset_class = 'FX' AND t.trade_date <= :as_of AND l.settle_date >= :as_of
ORDER BY i.instrument_id, l.settle_date
"""

_OPEN_FUTURE_SQL = """
SELECT DISTINCT i.instrument_id, i.bbg_ticker, l.settle_date
FROM trade_legs l JOIN trades_official t USING (trade_id) JOIN instruments i USING (instrument_id)
WHERE i.asset_class = 'FUTURE' AND t.trade_date <= :as_of AND l.settle_date >= :as_of
ORDER BY i.instrument_id, l.settle_date
"""

# A cross (neither leg is USD, e.g. EURSEK) needs its own outright, but delta/P&L still
# convert each leg's currency to USD at SPOT (CLAUDE.md's delta query: "the SPOT join ...
# is a LEFT JOIN so that a missing spot mark surfaces as a NULL delta; the engine must
# raise if any resulting delta is NULL, never drop the leg silently"). Found 2026-09-17
# (ladder agent): a book holding only a cross, with no direct trade in either leg's own
# USD pair, left both currencies' usd_delta NaN forever because nothing ever requested
# those USD pairs' SPOT. _cross_usd_legs below is that missing request, shared by
# build_requests (live pull) and inventory._needed_marks (coverage/diagnostics) so the two
# can never drift apart.
_MAJOR_QUOTE_CCYS = {"AUD", "EUR", "GBP", "NZD", "XAU", "XAG"}  # quote as XXXUSD; everything else is USDXXX

_OPEN_CROSS_LEG_CCYS_SQL = """
SELECT DISTINCT i.base_ccy, i.quote_ccy
FROM trade_legs l JOIN trades_official t USING (trade_id) JOIN instruments i USING (instrument_id)
WHERE i.asset_class = 'FX' AND t.trade_date <= :as_of AND l.settle_date >= :as_of
  AND i.base_ccy != 'USD' AND i.quote_ccy != 'USD'
"""


def _usd_pair_name(ccy: str) -> str:
    """Conventional USD-pair spelling for `ccy`: '<ccy>USD' for the majors/metals that
    quote against the dollar (AUD, EUR, GBP, NZD, XAU, XAG), 'USD<ccy>' for everything
    else -- the same convention CLAUDE.md's xlsx section and the delta query's pair join
    (`i.base_ccy || i.quote_ccy`) assume elsewhere."""
    return f"{ccy}USD" if ccy in _MAJOR_QUOTE_CCYS else f"USD{ccy}"


def _cross_usd_legs(conn: sqlite3.Connection, as_of_date: str) -> List[dict]:
    """One row per USD-conversion pair an open cross's legs need for their delta/P&L USD
    conversion: {ccy, pair_name, instrument_id, bbg_ticker, has_instrument}.

    `instrument_id`/`bbg_ticker` come from the `instruments` row when one already exists
    for the conventional pair name (canonical orientation, e.g. EURUSD, USDSEK); otherwise
    they are the conventional pair name itself and '<pair> Curncy' -- never written to
    `instruments` (this module never inserts instrument rows). `has_instrument` tells the
    caller whether a pulled SPOT for this pair can actually be written: `write_marks`
    silently drops any row whose instrument_id isn't a known instrument, so a caller that
    requests a `has_instrument=False` pair must report that gap explicitly (a warning
    naming the pair) rather than let it vanish silently."""
    ccys = set()
    for base, quote in conn.execute(_OPEN_CROSS_LEG_CCYS_SQL, {"as_of": as_of_date}):
        ccys.add(base)
        ccys.add(quote)
    out = []
    for ccy in sorted(ccys):
        pair_name = _usd_pair_name(ccy)
        row = conn.execute("SELECT instrument_id, bbg_ticker FROM instruments WHERE instrument_id = ?",
                           (pair_name,)).fetchone()
        if row is not None:
            out.append({"ccy": ccy, "pair_name": pair_name, "instrument_id": row[0], "bbg_ticker": row[1],
                       "has_instrument": True})
        else:
            out.append({"ccy": ccy, "pair_name": pair_name, "instrument_id": pair_name,
                       "bbg_ticker": f"{pair_name} Curncy", "has_instrument": False})
    return out


def build_requests(conn: sqlite3.Connection, as_of_date: str) -> list:
    """RequestRows per BUILD_PLAN.md section 2: one SPOT per open FX pair, one
    FWD_OUTRIGHT per (pair, open leg's own settle_date) -- no shared workbook maturity --
    one FUTURE_PX per open future at its own settle_date (expiry), and (2026-09-17) one
    extra SPOT per USD-conversion pair an open cross's legs need (see _cross_usd_legs) that
    isn't already covered by one of the pairs above."""
    from data.bloomberg.pull_marks import RequestRow
    fx_rows = conn.execute(_OPEN_FX_SQL, {"as_of": as_of_date}).fetchall()
    out, seen = [], set()
    for instrument_id, ticker, settle in fx_rows:
        if (instrument_id, "SPOT") not in seen:
            seen.add((instrument_id, "SPOT"))
            out.append(RequestRow(instrument_id, ticker, as_of_date, "SPOT"))
        if (instrument_id, "FWD_OUTRIGHT", settle) not in seen:
            seen.add((instrument_id, "FWD_OUTRIGHT", settle))
            out.append(RequestRow(instrument_id, ticker, settle, "FWD_OUTRIGHT"))
    for leg in _cross_usd_legs(conn, as_of_date):
        key = (leg["instrument_id"], "SPOT")
        if key not in seen:
            seen.add(key)
            out.append(RequestRow(leg["instrument_id"], leg["bbg_ticker"], as_of_date, "SPOT"))
    fut_rows = conn.execute(_OPEN_FUTURE_SQL, {"as_of": as_of_date}).fetchall()
    for instrument_id, ticker, settle in fut_rows:
        if (instrument_id, "FUTURE_PX", settle) not in seen:
            seen.add((instrument_id, "FUTURE_PX", settle))
            out.append(RequestRow(instrument_id, ticker, settle, "FUTURE_PX"))
    return out


# --------------------------------------------------------------------------- one pull
def _live_spot_rows(session, service, requests, as_of: date, diag, snapped: str):
    """Live PX_LAST via ReferenceDataRequest (intraday), unlike pull_marks' close-of-day
    historical path. Returns (rows, failures)."""
    from data.bloomberg.pull_marks import fetch_reference, BloombergRequestError
    spot_reqs = [r for r in requests if r.mark_type == "SPOT"]
    if not spot_reqs:
        return [], []
    tickers = sorted({r.bbg_ticker for r in spot_reqs})
    try:
        data = fetch_reference(session, service, tickers, ["PX_LAST"], diag=diag, tag={"purpose": "LIVE_SPOT"})
    except BloombergRequestError as exc:
        return [], [{"instrument_id": r.instrument_id, "mark_type": "SPOT", "settle_date": r.settle_date,
                     "detail": f"SPOT request failed: {exc}"} for r in spot_reqs]
    rows, failures = [], []
    for r in spot_reqs:
        value = data.get(r.bbg_ticker, {}).get("PX_LAST")
        if value is None:
            failures.append({"instrument_id": r.instrument_id, "mark_type": "SPOT", "settle_date": r.settle_date,
                             "detail": "no PX_LAST returned"})
            continue
        try:
            fvalue = float(value)
        except (TypeError, ValueError):
            failures.append({"instrument_id": r.instrument_id, "mark_type": "SPOT", "settle_date": r.settle_date,
                             "detail": f"PX_LAST not numeric: {value!r}"})
            continue
        rows.append({"as_of_date": as_of.isoformat(), "instrument_id": r.instrument_id,
                     "settle_date": as_of.isoformat(), "mark_type": "SPOT", "value": fvalue,
                     "source": SRC_SPOT_FWD, "snapped_at": snapped})
    return rows, failures


def _fwd_outright_rows(session, service, fwd_reqs, as_of: date, spot_by_pair: Dict[str, float],
                       snapped: str) -> Tuple[List[dict], List[str], List[dict]]:
    """FWD_OUTRIGHT per (pair, settle date) from the bulk FWD_CURVE table (one request
    per cycle for all pairs). EXACT tenor -> BBG_BFXFORWARD; interpolated -> BBG_INTERP
    (never official). Returns (rows, warnings, failures).

    A leg settling on or before `as_of` is marked directly at that pair's live SPOT
    (source BBG_BFXFORWARD -- still official, not a fallback) rather than through the
    curve at all: FWD_CURVE's points start at the first standard tenor *after* spot, so
    fwd_curve.outright_for_date has no interpolation range when settle_date == as_of --
    its from-spot branch only fires for spot_date < target, never ==. Found on the
    Bloomberg PC 2026-09-17: USDMXN settling the same calendar day it was pulled came back
    MISSING every cycle for exactly this reason. `past` settle dates (< as_of) never reach
    this function at all (pull_once filters those out as SKIPPED before calling it); this
    handles the boundary day itself."""
    from data.bloomberg.fwd_curve import outright_for_date, request_fwd_curves
    from data.bloomberg.pull_marks import _get_blpapi
    if not fwd_reqs:
        return [], [], []
    rows, warnings, failures = [], [], []
    today_reqs = [r for r in fwd_reqs if date.fromisoformat(r.settle_date) <= as_of]
    curve_reqs = [r for r in fwd_reqs if date.fromisoformat(r.settle_date) > as_of]
    for r in today_reqs:
        spot = spot_by_pair.get(r.instrument_id)
        if spot is None:
            failures.append({"instrument_id": r.instrument_id, "mark_type": "FWD_OUTRIGHT", "settle_date": r.settle_date,
                             "detail": f"settles on or before {as_of.isoformat()} but no live SPOT for "
                                       f"{r.instrument_id} was pulled this cycle to mark it at"})
            continue
        rows.append({"as_of_date": as_of.isoformat(), "instrument_id": r.instrument_id, "settle_date": r.settle_date,
                     "mark_type": "FWD_OUTRIGHT", "value": float(spot), "source": SRC_SPOT_FWD, "snapped_at": snapped,
                     "detail": "settles today: marked at spot"})
    if not curve_reqs:
        return rows, warnings, failures
    blpapi = _get_blpapi()
    tickers = sorted({r.bbg_ticker for r in curve_reqs})
    try:
        curves = request_fwd_curves(blpapi, session, service, tickers)
    except Exception as exc:  # network layer raised: every forward fails with that reason
        failures += [{"instrument_id": r.instrument_id, "mark_type": "FWD_OUTRIGHT", "settle_date": r.settle_date,
                     "detail": f"FWD_CURVE request raised: {exc!r}"} for r in curve_reqs]
        return rows, warnings, failures
    for r in curve_reqs:
        curve = curves.get(r.bbg_ticker, {"points": [], "error": "no curve"})
        if not curve["points"]:
            failures.append({"instrument_id": r.instrument_id, "mark_type": "FWD_OUTRIGHT", "settle_date": r.settle_date,
                             "detail": f"FWD_CURVE: {curve.get('error', 'no points')}"})
            continue
        target = date.fromisoformat(r.settle_date)
        value, how = outright_for_date(curve["points"], target, spot_by_pair.get(r.instrument_id), as_of)
        if value is None:
            failures.append({"instrument_id": r.instrument_id, "mark_type": "FWD_OUTRIGHT", "settle_date": r.settle_date,
                             "detail": f"{r.settle_date} outside curve {curve['points'][0][0]}..{curve['points'][-1][0]}"
                                       + (" and no live spot for near-date interpolation" if how == "" and
                                          spot_by_pair.get(r.instrument_id) is None else "")})
            continue
        source = SRC_SPOT_FWD if how == "EXACT" else SRC_INTERP
        if how != "EXACT":
            warnings.append(f"{r.instrument_id} {r.settle_date}: {how.lower()} from FWD_CURVE tenors (source {SRC_INTERP})")
        rows.append({"as_of_date": as_of.isoformat(), "instrument_id": r.instrument_id, "settle_date": r.settle_date,
                     "mark_type": "FWD_OUTRIGHT", "value": float(value), "source": source, "snapped_at": snapped})
    return rows, warnings, failures


def write_marks(conn: sqlite3.Connection, rows: List[dict]) -> int:
    """INSERT OR REPLACE so each 2-minute cycle refreshes the same key with a new
    snapped_at. Only known instruments; anything else is skipped and reported."""
    known = {r[0] for r in conn.execute("SELECT instrument_id FROM instruments")}
    n = 0
    with conn:
        for r in rows:
            if r["instrument_id"] not in known:
                continue
            conn.execute("INSERT OR REPLACE INTO marks VALUES (?,?,?,?,?,?,?)",
                         (r["as_of_date"], r["instrument_id"], r["settle_date"], r["mark_type"],
                          float(r["value"]), r["source"], r["snapped_at"]))
            n += 1
    return n


_OPEN_OPTION_CCYS_SQL = """
SELECT DISTINCT i.base_ccy, i.quote_ccy
FROM trades_official t JOIN instruments i USING (instrument_id)
WHERE t.product = 'FX_OPTION' AND t.trade_date <= :as_of AND i.expiry_date >= :as_of
  AND t.trade_id NOT IN (SELECT trade_id FROM realised_pnl)
"""


def _rates_step(conn: sqlite3.Connection, today: date, host: str, port: int, rates_source=None) -> dict:
    """Pull OIS quotes and fixings for every currency with an un-matured IRS trade, PLUS
    every currency of an open FX_OPTION's underlying pair (2026-09-17 fix): each option
    needs a domestic AND a foreign discount curve
    (engine/options/inputs.py::resolve_market_inputs -> .rates.resolve_fx_rates), and
    before this fix nothing ever pulled curve_quotes for an option-only currency, so any
    option in a currency with no IRS trade in the book fell straight to "no curve/rate
    <CCY>" -- found live on the Bloomberg PC (EUR/SEK/JPY options, no IRS at all). Then
    prices every IRS. `rates_source` (anything with `get_curve_quotes` / `get_fixings`,
    e.g. `RatesFileSource`) is injectable for tests; default is a live
    `RatesBloombergSource` on `host:port`. Never raises: per-currency and per-trade
    failures are reported in the returned dict (`{skipped}` when there is neither an IRS
    nor an FX_OPTION).

    A currency outside the Phase 1 OIS set (`rates_marketdata.OIS_INDEX` --
    USD/EUR/GBP/JPY/CHF/CAD/AUD) is never sent to Bloomberg at all (there is no curve to
    ask for -- SEK, for one, has no OIS index in scope per CLAUDE.md); it is recorded
    directly with a plain-English reason instead, so a per-option "no curve/rate" skip
    (engine/options always falls back to a manual rate for these) can name the missing
    curve rather than a bare currency code."""
    out: dict = {"currencies": {}, "priced": 0, "failed": [], "as_of_date": today.isoformat()}
    irs_ccys = {r[0] for r in conn.execute(
        "SELECT DISTINCT i.base_ccy FROM trades_official t JOIN instruments i USING (instrument_id) "
        "WHERE t.product = 'IRS' AND t.trade_date <= ? "
        "AND t.trade_id NOT IN (SELECT trade_id FROM realised_pnl)", (today.isoformat(),))}
    option_ccys: set = set()
    for base, quote in conn.execute(_OPEN_OPTION_CCYS_SQL, {"as_of": today.isoformat()}):
        option_ccys.add(base)
        option_ccys.add(quote)
    ccys = sorted(irs_ccys | option_ccys)
    if not ccys:
        out["skipped"] = "no IRS or FX_OPTION trades to price"
        return out
    try:
        from data.bloomberg import rates_marketdata as rm
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"rates_marketdata not importable: {exc!r}"
        return out
    in_scope = [c for c in ccys if c in rm.OIS_INDEX]
    out_of_scope = [c for c in ccys if c not in rm.OIS_INDEX]
    for ccy in out_of_scope:
        out["currencies"][ccy] = {
            "quotes": 0, "fixings": 0,
            "error": f"{ccy} has no OIS index in Phase 1 scope ({', '.join(sorted(rm.OIS_INDEX))} only); "
                     "enter a manual rate (engine/options/rates.py::set_manual_rate) instead.",
        }
    if in_scope:
        if rates_source is None:
            try:
                rates_source = rm.RatesBloombergSource(host=host, port=port)
            except Exception as exc:  # noqa: BLE001
                out["error"] = f"RatesBloombergSource unavailable: {exc!r}"
                return out
        for ccy in in_scope:
            entry = {"quotes": 0, "fixings": 0, "error": ""}
            try:
                snap = rates_source.get_curve_quotes(ccy, today)
                entry["quotes"] = rm.write_curve_quotes(conn, snap, today.isoformat())
                # Fixings value a seasoned IRS's current float period; an FX_OPTION needs
                # a discount curve only, never an overnight fixing history, so an
                # option-only currency here (2026-09-17) must not attempt this call --
                # RatesFileSource/RatesBloombergSource routinely have no fixings staged
                # for a currency that was only ever added for its curve, and that would
                # otherwise overwrite this entry's error with an unrelated fixings failure
                # even though the curve pull above succeeded fine.
                if ccy in irs_ccys:
                    first = conn.execute(
                        "SELECT MIN(l.start_date) FROM trade_legs l JOIN trades_official t USING (trade_id) "
                        "JOIN instruments i USING (instrument_id) WHERE t.product = 'IRS' AND i.base_ccy = ?", (ccy,)).fetchone()[0]
                    start = date.fromisoformat(first) if first else today
                    if start <= today:
                        fixings = rates_source.get_fixings(ccy, start, today)
                        entry["fixings"] = rm.write_fixings(conn, ccy, fixings)
            except Exception as exc:  # noqa: BLE001
                entry["error"] = f"{type(exc).__name__}: {exc}"
            out["currencies"][ccy] = entry
        close = getattr(rates_source, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass
    try:
        from engine.rates.store import price_all_and_store
        results = price_all_and_store(conn, today.isoformat())
        out["priced"] = sum(1 for r in results if r["ok"])
        out["failed"] = [{"trade_id": r["trade_id"], "error": r["error"]} for r in results if not r["ok"]]
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"pricing failed: {exc!r}"
    return out


def _vol_step(conn: sqlite3.Connection, today: date, host: str, port: int, vol_source=None) -> dict:
    """Pull the FX vol smile (ATM/RR/BF) for every pair with an open FX_OPTION into
    `vol_quotes`, so engine/options has something to resolve from (2026-09-17 fix): this
    pull never ran at all before -- live.py had no call into
    data/bloomberg/vol_marketdata.py anywhere -- so every option fell through SMILE and
    ATM_INTERP straight to the MANUAL option_vols fallback, which nobody had entered
    either, and was skipped "no vol". `vol_source` (e.g.
    data.bloomberg.vol_marketdata.VolFileSource) is injectable for tests; default is a
    live `VolBloombergSource` on `host:port`. Never raises: `{skipped}` when there is no
    open FX_OPTION; a per-ticker failure is recorded in `diagnostics`
    (pair/tenor/quote_type/ticker/detail, exactly VolFetchResult.diagnostics) rather than
    only a currency/pair name, so `_options_step` can name the specific failing Bloomberg
    ticker in a "no vol" skip reason (item 3c) -- see that function's `vol_diagnostics`
    parameter."""
    out: dict = {"pairs": {}, "written": 0, "diagnostics": [], "as_of_date": today.isoformat()}
    n = conn.execute("SELECT COUNT(*) FROM trades_official WHERE product = 'FX_OPTION' AND trade_date <= ?",
                     (today.isoformat(),)).fetchone()[0]
    if not n:
        out["skipped"] = "no FX_OPTION trades to price"
        return out
    try:
        from data.bloomberg import vol_marketdata as vm
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"vol_marketdata not importable: {exc!r}"
        return out
    pairs = vm.vol_pairs_needed(conn)
    if vol_source is None:
        try:
            vol_source = vm.VolBloombergSource(host=host, port=port)
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"VolBloombergSource unavailable: {exc!r}"
            return out
    try:
        result = vol_source.get_vol_quotes(pairs)  # as_of=None: live ReferenceDataRequest, source BBG_BDP
        out["written"] = vm.write_vol_quotes(conn, result, today.isoformat(), source=result.source)
        out["diagnostics"] = list(result.diagnostics)
        out["pairs"] = {p: len(pv.quotes) for p, pv in result.pairs.items()}
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    close = getattr(vol_source, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001
            pass
    return out


def _enrich_no_vol_reason(conn: sqlite3.Connection, trade_id: str, reason: str, vol_diagnostics: List[dict]) -> str:
    """Turn a bare "no vol" skip reason into one naming the specific Bloomberg ticker(s)
    that failed for this trade's pair (item 3c), by cross-referencing
    _vol_step's `diagnostics` (one entry per failed ticker, each carrying its own `pair`)
    against the option's own pair. Returns `reason` unchanged if the trade's pair can't be
    found or nothing in `vol_diagnostics` matches it (e.g. the pair priced from
    ATM_INTERP/MANUAL and failed for an unrelated reason)."""
    row = conn.execute(
        "SELECT i.base_ccy || i.quote_ccy FROM trades t JOIN instruments i USING (instrument_id) "
        "WHERE t.trade_id = ?", (trade_id,)).fetchone()
    pair = row[0] if row else None
    if pair is None:
        return reason
    tickers = sorted({d["ticker"] for d in vol_diagnostics if d.get("pair") == pair and d.get("ticker")})
    if not tickers:
        return reason
    shown = ", ".join(tickers[:3]) + (f" (+{len(tickers) - 3} more)" if len(tickers) > 3 else "")
    return (f"{reason}: no vol_quotes for {pair} -- Bloomberg returned nothing for {shown}; run "
            "py -3 -m data.bloomberg.vol_marketdata --probe on the Bloomberg PC to check these tickers.")


def _options_step(conn: sqlite3.Connection, today: date, vol_diagnostics: Optional[List[dict]] = None) -> dict:
    """Price every FX option through engine/options (PREMIUM + Greeks). Never raises.
    `vol_diagnostics` (see _vol_step) is used only to enrich a "no vol" skip reason with
    the specific failing Bloomberg ticker(s) for that trade's pair (item 3c, 2026-09-17)."""
    out: dict = {"priced": 0, "skipped": [], "as_of_date": today.isoformat()}
    n = conn.execute("SELECT COUNT(*) FROM trades_official WHERE product = 'FX_OPTION' AND trade_date <= ?",
                     (today.isoformat(),)).fetchone()[0]
    if not n:
        out["skipped"] = "no FX_OPTION trades to price"
        return out
    try:
        from engine.options.store import price_all_and_store
        outcomes = price_all_and_store(conn, today.isoformat())
        out["priced"] = sum(1 for o in outcomes if getattr(o, "priced", False))
        skipped = []
        for o in outcomes:
            if getattr(o, "priced", False):
                continue
            reason = getattr(o, "skip_reason", "") or getattr(o, "reason", "")
            if reason == "no vol" and vol_diagnostics:
                reason = _enrich_no_vol_reason(conn, o.trade_id, reason, vol_diagnostics)
            skipped.append({"trade_id": o.trade_id, "reason": reason})
        out["skipped"] = skipped
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{exc!r}"
    return out


def pull_once(db_path, as_of_date: Optional[str] = None, host: str = "localhost", port: int = 8194,
              session_factory: Optional[Callable] = None, today: Optional[date] = None,
              rates_source=None, vol_source=None) -> dict:
    """One full cycle. Returns and writes the status dict. Never raises: any exception
    becomes connected=False with the traceback in `reason`. `today` (live mark date)
    defaults to the wall-clock date; injectable for tests. `vol_source` mirrors
    `rates_source`'s injection for _vol_step (data.bloomberg.vol_marketdata's live/file
    source)."""
    from data.ingest.schema import connect
    started = _now_iso()
    status = {"time": started, "connected": False, "reason": "", "host": f"{host}:{port}",
              "as_of_date": as_of_date, "requested": 0, "written": 0, "failed": 0, "items": [], "warnings": []}
    try:
        ok, why = availability(host, port) if session_factory is None else (True, "")
        if not ok:
            status["reason"] = why
            write_status(db_path, status)
            return status
        from data.bloomberg import pull_marks as pm
        conn = connect(Path(db_path))
        try:
            # The book date defaults to the live mark date (2026-09-17 fix). It used to
            # default to MAX(positions.as_of_date) -- the last BNP snapshot date, a
            # source that no longer feeds the app -- so on a database still carrying
            # the 2026-08-17 BNP positions the feed built its request list as of that
            # day and never asked for a mark on any trade dated after it.
            today = today or book_today()
            if as_of_date is None:
                as_of_date = today.isoformat()
            status["as_of_date"] = as_of_date
            requests = build_requests(conn, as_of_date)
            status["requested"] = len(requests)
            if not requests:
                # Nothing FX-shaped to price, but swaps and options may still need a
                # curve / premium refresh (2026-09-17) before the FX-only early return.
                today = today or book_today()
                status["rates"] = _rates_step(conn, today, host, port, rates_source)
                status["vol"] = _vol_step(conn, today, host, port, vol_source)
                status["options"] = _options_step(conn, today, status["vol"].get("diagnostics"))
                status.update(connected=True, reason="no open FX legs or futures to price")
                write_status(db_path, status)
                return status
            diag = pm.Diagnostics()
            if session_factory is None:
                session, service = pm.open_session(host, port, diag)
            else:
                session, service = session_factory()
            snapped = _now_iso()  # live pull: real wall-clock time, not the 15:00 NY convention
            today = today or book_today()
            spot_rows, spot_fail = _live_spot_rows(session, service, requests, today, diag, snapped)
            spot_by_pair = {r["instrument_id"]: r["value"] for r in spot_rows}
            # A forward whose settle date is already past (trade settled since the snapshot)
            # has nothing to price: reported SKIPPED, never FAILED, never counted as missing.
            past = {(r.instrument_id, r.settle_date) for r in requests
                    if r.mark_type == "FWD_OUTRIGHT" and date.fromisoformat(r.settle_date) < today}
            fwd_reqs = [r for r in requests if r.mark_type == "FWD_OUTRIGHT" and (r.instrument_id, r.settle_date) not in past]
            fwd_rows, fwd_warnings, fwd_fail = _fwd_outright_rows(session, service, fwd_reqs, today, spot_by_pair, snapped)
            fut_reqs = [r for r in requests if r.mark_type == "FUTURE_PX"]
            # live=True: PX_SETTLE for `today` has nothing to return before that day's US
            # close, so the live pull tries PX_LAST first and only falls back to the
            # latest prior PX_SETTLE if that's also empty (2026-09-17 fix, ESU6 Index
            # found MISSING every cycle before the close). Historical backfill
            # (data.bloomberg.backfill.py) always requests a past, already-closed date and
            # keeps calling this with the default live=False.
            fut_rows, fut_warnings, fut_fail = pm.build_future_rows(session, service, fut_reqs, today, diag, live=True) \
                if fut_reqs else ([], [], [])
            # A cross's USD-conversion leg with no instrument row on file (see
            # _cross_usd_legs) is still requested from Bloomberg above, but write_marks
            # below can never persist it (no instrument to satisfy the FK-like
            # relationship) -- surface that gap explicitly instead of letting the mark
            # silently vanish (2026-09-17, ladder agent's usd_delta NaN report).
            cross_leg_warnings = [
                f"{leg['pair_name']}: needed to convert {leg['ccy']} to USD for a cross's delta/P&L, but no "
                f"'{leg['pair_name']}' instrument is on file -- its SPOT cannot be written even when Bloomberg "
                "returns a price."
                for leg in _cross_usd_legs(conn, as_of_date) if not leg["has_instrument"]
            ]
            warnings = fwd_warnings + fut_warnings + cross_leg_warnings
            rows = spot_rows + fwd_rows + fut_rows
            written = write_marks(conn, rows)
            # Rates (2026-09-17): OIS curve quotes + fixings per swap currency AND per open
            # FX_OPTION's pair currencies into curve_quotes / index_fixings, then every IRS
            # priced (PV_USD / DV01_USD / CASHFLOW_USD / PAR_RATE, source QL_PRICER). Vol
            # (2026-09-17): the FX vol smile (ATM/RR/BF) into vol_quotes for every open
            # option's pair. Then every FX option priced (PREMIUM and Greeks, source
            # QL_OPTIONS_PRICER), with the vol step's per-ticker diagnostics available to
            # enrich a "no vol" skip reason. All three before realise_settled so a swap
            # maturing today or an option expiring today freezes at today's mark.
            status["rates"] = _rates_step(conn, today, host, port, rates_source)
            status["vol"] = _vol_step(conn, today, host, port, vol_source)
            status["options"] = _options_step(conn, today, status["vol"].get("diagnostics"))
            # Realisation only (BUILD_PLAN.md section 6, Task B): freeze FX trades whose
            # settle date is before `today` and are not yet in realised_pnl. The daily LTD
            # snapshot itself is engine/pnl/ledger.py's job (task A); this module only
            # calls the one guarded function it needs and never writes pnl_snapshots.
            try:
                from engine.pnl.ledger import realise_settled
                led = realise_settled(conn, today.isoformat())
                status["ledger"] = {"as_of_date": today.isoformat(), "realised": led.get("realised"),
                                    "unrealisable": led.get("unrealisable")}
            except ImportError as exc:
                status["ledger"] = {"skipped": f"engine.pnl.ledger.realise_settled not importable: {exc!r}"}
            except Exception as exc:
                status["ledger"] = {"error": f"{exc!r}"}
                status.setdefault("warnings", []).append(f"realise_settled failed: {exc!r}")
            status.update(connected=True, written=written, warnings=list(warnings)[:50],
                          as_of_marks=today.isoformat())
            ok_keys = {(r["instrument_id"], r["mark_type"], r["settle_date"]): r for r in rows}
            items = []
            for r in requests:
                settle = today.isoformat() if r.mark_type == "SPOT" else r.settle_date
                if (r.instrument_id, r.settle_date) in past and r.mark_type == "FWD_OUTRIGHT":
                    items.append({"instrument_id": r.instrument_id, "mark_type": r.mark_type, "settle_date": settle,
                                  "status": "SKIPPED", "value": None, "source": "",
                                  "detail": f"settle date already past on {today}: settled, nothing to price"})
                    continue
                hit = ok_keys.get((r.instrument_id, r.mark_type, settle))
                if hit:
                    items.append({"instrument_id": r.instrument_id, "mark_type": r.mark_type, "settle_date": settle,
                                  "status": "OK", "value": hit["value"], "source": hit["source"],
                                  "detail": hit.get("detail", "")})
                else:
                    detail = next((f.get("detail", "") for f in spot_fail + fwd_fail + fut_fail
                                   if f.get("instrument_id") == r.instrument_id and f.get("mark_type") == r.mark_type
                                   and (r.mark_type == "SPOT" or f.get("settle_date") == r.settle_date)), "not returned")
                    items.append({"instrument_id": r.instrument_id, "mark_type": r.mark_type, "settle_date": settle,
                                  "status": "FAILED", "value": None, "source": "", "detail": detail})
            status["items"] = items
            status["failed"] = sum(1 for i in items if i["status"] == "FAILED")
            status["skipped"] = sum(1 for i in items if i["status"] == "SKIPPED")
        finally:
            conn.close()
    except Exception:
        status["connected"] = False
        status["reason"] = "pull failed: " + traceback.format_exc(limit=3).strip().splitlines()[-1]
        status["traceback"] = traceback.format_exc()
    write_status(db_path, status)
    return status


# --------------------------------------------------------------------------- feed thread
@dataclass
class LiveFeed:
    db_path: Path
    interval: int = INTERVAL_SECONDS
    host: str = "localhost"
    port: int = 8194
    last_status: Optional[dict] = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _wake: threading.Event = field(default_factory=threading.Event)
    _thread: Optional[threading.Thread] = None

    def start(self) -> "LiveFeed":
        self._thread = threading.Thread(target=self._loop, name="bloomberg-live-feed", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def trigger_now(self) -> None:
        """Wake the feed loop immediately instead of waiting out the rest of the 2-minute
        interval, and run one extra cycle right away. Call this after any event that could
        change what needs pricing -- most importantly a blotter import landing new trades
        (data.ingest.upload.import_blotter / ui/uploads.py) -- so marks for trades a user
        just uploaded are pulled within seconds rather than up to INTERVAL_SECONDS later.

        Root cause this exists for (found 2026-09-17): the feed's very first pull runs the
        instant the app starts (LiveFeed._loop below), typically before any trade has been
        uploaded through the browser, so build_requests() finds nothing to price and the
        cycle writes {requested: 0, written: 0, connected: True}. Nothing then re-triggers
        a pull until the next scheduled tick (up to INTERVAL_SECONDS away), so a user who
        uploads a blotter and immediately checks diagnostics can see that stale empty pull
        reported as if it were current. ui/uploads.py (owned by ui-shell, not bbg-data)
        must call `app.bloomberg_feed.trigger_now()` after a successful import_blotter()
        for this to take effect end to end; see the bbg-data agent's handoff note for the
        exact call site."""
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.last_status = pull_once(self.db_path, host=self.host, port=self.port)
                from data.bloomberg.backfill import start_auto_backfill
                start_auto_backfill(self.db_path, host=self.host, port=self.port)
            except Exception:  # pull_once already catches; this guards the thread itself
                write_status(self.db_path, {"time": _now_iso(), "connected": False,
                                            "reason": "feed thread error: " + traceback.format_exc().strip().splitlines()[-1],
                                            "traceback": traceback.format_exc(), "requested": 0, "written": 0,
                                            "failed": 0, "items": [], "warnings": []})
            self._wake.wait(self.interval)
            self._wake.clear()


def start_feed_if_available(db_path, host: str = "localhost", port: int = 8194,
                            interval: int = INTERVAL_SECONDS) -> Tuple[Optional[LiveFeed], str]:
    """Start the 2-minute feed when Bloomberg is reachable; otherwise write a status file
    explaining why and return (None, reason). Never fabricates data either way."""
    ok, why = availability(host, port)
    if not ok:
        write_status(db_path, {"time": _now_iso(), "connected": False, "reason": why,
                               "host": f"{host}:{port}", "requested": 0, "written": 0, "failed": 0,
                               "items": [], "warnings": []})
        return None, why
    # Record immediately that the feed thread exists, so the UI never says "no pull recorded"
    # while the first pull is in flight.
    write_status(db_path, {"time": _now_iso(), "connected": False,
                           "reason": "feed started; first Bloomberg pull in progress", "host": f"{host}:{port}",
                           "requested": 0, "written": 0, "failed": 0, "items": [], "warnings": []})
    return LiveFeed(Path(db_path), interval, host, port).start(), ""


# --------------------------------------------------------------------------- rates for the ladder
_LATEST_SPOT_SQL = """
SELECT m.instrument_id, i.base_ccy, i.quote_ccy, m.value, m.source, m.snapped_at, m.as_of_date
FROM marks_official m JOIN instruments i USING (instrument_id)
WHERE m.mark_type = 'SPOT' AND i.asset_class = 'FX'
ORDER BY m.instrument_id, m.as_of_date, m.snapped_at
"""


def rates_from_marks(conn: sqlite3.Connection, now: Optional[datetime] = None,
                     stale_after_seconds: int = STALE_AFTER_SECONDS) -> Dict[str, dict]:
    """currency -> {rate, inverted, source, timestamp, stale} from the LATEST official SPOT
    mark per USD pair (last as_of_date, then last snapped_at). USDXXX pairs are inverted.
    Crosses are ignored. A currency with no mark is simply absent (-> MISSING_RATE).
    stale = snapped_at older than stale_after_seconds relative to `now`."""
    now = now or datetime.now(timezone.utc)
    latest: Dict[str, tuple] = {}
    for pair, base, quote, value, source, snapped, as_of in conn.execute(_LATEST_SPOT_SQL):
        if "USD" not in (base, quote):
            continue
        latest[pair] = (base, quote, value, source, snapped, as_of)  # ordered ascending: last wins
    out: Dict[str, dict] = {}
    for pair, (base, quote, value, source, snapped, as_of) in latest.items():
        ccy = quote if base == "USD" else base
        stale = True
        try:
            ts = datetime.fromisoformat(snapped)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            stale = (now - ts) > timedelta(seconds=stale_after_seconds)
        except ValueError:
            pass
        out[ccy] = {"rate": float(value), "inverted": base == "USD", "source": source,
                    "timestamp": snapped, "stale": stale, "as_of_date": as_of, "pair": pair}
    return out


# --------------------------------------------------------------------------- CLI
def _print_status(status: Optional[dict]) -> None:
    if not status:
        print("No Bloomberg status recorded yet.")
        return
    print(f"time {status.get('time')}  connected {status.get('connected')}  {status.get('reason', '')}")
    print(f"as_of {status.get('as_of_date')}  requested {status.get('requested')}  written {status.get('written')}"
          f"  failed {status.get('failed')}")
    for it in status.get("items", []):
        val = "" if it.get("value") is None else f"{it['value']:.8f}"
        print(f"  {it['status']:6s} {it['instrument_id']:8s} {it['mark_type']:12s} {it['settle_date']}  {val:>16s}  "
              f"{it.get('source', '')}  {it.get('detail', '')}")
    for w in status.get("warnings", []):
        print("  warning:", w)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Bloomberg live feed diagnostics / single pull.")
    parser.add_argument("--db", default=None, help="SQLite path (default: ui.app.get_db_path())")
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8194)
    parser.add_argument("--once", action="store_true", help="run one pull now and print its status")
    parser.add_argument("--status", action="store_true", help="print the last recorded status")
    args = parser.parse_args(argv)
    if args.db is None:
        from ui.app import get_db_path
        args.db = get_db_path()
    if args.once:
        status = pull_once(args.db, args.as_of, args.host, args.port)
    else:
        status = read_status(args.db)
        if status is None:
            ok, why = availability(args.host, args.port)
            status = {"time": _now_iso(), "connected": ok, "reason": why or "no pull has run yet",
                      "requested": 0, "written": 0, "failed": 0, "items": []}
    _print_status(status)
    return 0 if status.get("connected") and not status.get("failed") else 1


if __name__ == "__main__":
    sys.exit(main())
