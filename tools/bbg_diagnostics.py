#!/usr/bin/env python
"""Bloomberg diagnostics matching the ui-shell "Check Bloomberg connection" button
interface described in ui/tabs/header.py:

    run_bloomberg_diagnostics() -> list[{"name": str, "status": "pass"|"fail"|"warning",
                                          "message": str}]

This module is READ-ONLY: it opens the database read-only where possible and never
writes marks, curves, or any application table. It imports (never edits) data/bloomberg
and data/ingest modules to reuse their request-building and official-source logic, so
its checks stay in sync with what the app actually does rather than re-deriving it.

Written by bbg-diagnostics (read-only auditor). Lives in tools/ rather than
data/bloomberg/ because that directory is owned by bbg-data; see
docs/open-questions.md / the bbg-diagnostics handoff note for the one-line change
bbg-data needs to make (`data/bloomberg/bbg_diagnostics.py` re-exporting
`run_bloomberg_diagnostics` from here) so ui/tabs/header.py's lazy import finds it.

CLI:
    py -3 tools\\bbg_diagnostics.py
    py -3 tools\\bbg_diagnostics.py --db data\\raw\\risk.db --as-of 2026-09-16

Checks (each becomes one or more result rows):
  1. Session connectivity      -- blpapi import + TCP + Session.start + //blp/refdata
  2. Official-source mapping   -- schema.OFFICIAL_MARK_SOURCE matches CLAUDE.md's table
                                   exactly, and marks_official never resolves a mark_type
                                   to BNP_BVAL or BBG_INTERP (or BBG_BDH for the three
                                   IRS mark_types, now reconciliation-only)
  3. FX marks coverage         -- every open FX pair/leg's SPOT and FWD_OUTRIGHT is
                                   OFFICIAL (not MISSING/INTERP/MANUAL fallback) as of
                                   the given date, via data.bloomberg.inventory
  4. Futures marks coverage    -- every open future's FUTURE_PX is OFFICIAL
  5. IRS / OIS curve coverage  -- curve_quotes has today's quotes for every OIS index in
                                   scope that a live IRS trade needs, and PAR_RATE/PV_USD/
                                   DV01_USD marks (where present) are QL_PRICER, never
                                   BBG_BDH, as official
  5b. Overnight index fixings  -- a seasoned swap has its index's fixings on file from
                                   its effective date to as_of (engine/rates needs them)
  5c. FX option coverage       -- every open option has a strike on file (else it can
                                   never be priced) and an official PREMIUM and DELTA
  5d. Clock                    -- local time vs New York, and whether as_of is today NY
  6. snapped_at offset         -- official marks carry a timezone-resolved snapped_at
                                   (CLAUDE.md: 17:00 America/New_York close), not a naive
                                   timestamp
  7. Last live feed pull       -- data.bloomberg.live's last recorded status
  8. Unverified assumptions    -- how many FX vol ticker/field guesses in
                                   data/bloomberg/vol_marketdata.py are still UNVERIFIED
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import traceback
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

Check = Dict[str, str]


def _row(name: str, status: str, message: str) -> Check:
    assert status in ("pass", "fail", "warning"), status
    return {"name": name, "status": status, "message": message}


def _default_db_path() -> Optional[Path]:
    try:
        from ui.app import get_db_path
        return get_db_path()
    except Exception:
        env = os.environ.get("RISK_DB")
        return Path(env) if env else None


def _today_ny() -> str:
    """The book date the live feed stamps marks with: today in America/New_York.
    (`positions` is BNP-fed and inert since 2026-09-17, so it is no longer used as the
    default here -- it would point every coverage check at 2026-08-17 forever.)"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return date.today().isoformat()


def _latest_as_of(conn: sqlite3.Connection) -> str:
    return _today_ny()


# --------------------------------------------------------------------------- 1. session
def check_session(host: str, port: int) -> List[Check]:
    try:
        from data.bloomberg.live import availability
    except Exception as exc:
        return [_row("Bloomberg session connectivity", "fail",
                     f"Could not load the connectivity check itself ({exc.__class__.__name__}); "
                     "data/bloomberg/live.py may be broken.")]
    try:
        ok, reason = availability(host=host, port=port)
    except Exception as exc:
        return [_row("Bloomberg session connectivity", "fail",
                     f"Connectivity check raised an unexpected error ({exc.__class__.__name__}).")]
    if ok:
        return [_row("Bloomberg session connectivity", "pass",
                     f"blpapi is installed and {host}:{port} accepted a connection.")]
    return [_row("Bloomberg session connectivity", "fail", f"{reason}.")]


# --------------------------------------------------------------------------- 2. official-source mapping
_EXPECTED_OFFICIAL = {
    # CLAUDE.md "Official marks" table, restated here on purpose (not imported from
    # data/ingest/schema.py) so that a drift between the schema and the contract is
    # reported instead of silently agreed with. Updated 2026-09-17: DELTA/PREMIUM and the
    # Greeks moved from MANUAL to QL_OPTIONS_PRICER; CASHFLOW_USD added under QL_PRICER.
    "SPOT": "BBG_BFXFORWARD",
    "FWD_OUTRIGHT": "BBG_BFXFORWARD",
    "FUTURE_PX": "BBG_BDH",
    "PAR_RATE": "QL_PRICER",
    "PV_USD": "QL_PRICER",
    "DV01_USD": "QL_PRICER",
    "CASHFLOW_USD": "QL_PRICER",
    "DELTA": "QL_OPTIONS_PRICER",
    "DELTA_PA": "QL_OPTIONS_PRICER",
    "PREMIUM": "QL_OPTIONS_PRICER",
    "GAMMA": "QL_OPTIONS_PRICER",
    "THETA": "QL_OPTIONS_PRICER",
    "VEGA": "QL_OPTIONS_PRICER",
    "RHO": "QL_OPTIONS_PRICER",
}
_NEVER_OFFICIAL = {"BNP_BVAL", "BBG_INTERP"}


def check_official_source_mapping(conn: Optional[sqlite3.Connection]) -> List[Check]:
    out: List[Check] = []
    try:
        from data.ingest.schema import OFFICIAL_MARK_SOURCE
    except Exception as exc:
        return [_row("Official-source mapping", "fail",
                     f"Could not import data.ingest.schema.OFFICIAL_MARK_SOURCE ({exc.__class__.__name__}).")]

    mismatches = [f"{mt}: schema says {OFFICIAL_MARK_SOURCE.get(mt)!r}, CLAUDE.md says {src!r}"
                  for mt, src in _EXPECTED_OFFICIAL.items() if OFFICIAL_MARK_SOURCE.get(mt) != src]
    bad_official = {mt: src for mt, src in OFFICIAL_MARK_SOURCE.items() if src in _NEVER_OFFICIAL}
    if mismatches or bad_official:
        detail = "; ".join(mismatches + [f"{mt} wrongly points at {src} (reconciliation-only)"
                                          for mt, src in bad_official.items()])
        out.append(_row("Official-source mapping matches CLAUDE.md", "fail",
                         f"OFFICIAL_MARK_SOURCE has drifted from the contract: {detail}."))
    else:
        out.append(_row("Official-source mapping matches CLAUDE.md", "pass",
                         "SPOT/FWD_OUTRIGHT->BBG_BFXFORWARD, FUTURE_PX->BBG_BDH, "
                         "PAR_RATE/PV_USD/DV01_USD/CASHFLOW_USD->QL_PRICER, "
                         "PREMIUM/DELTA/Greeks->QL_OPTIONS_PRICER, as specified."))

    if conn is None:
        out.append(_row("marks_official never resolves to a reconciliation-only source", "warning",
                         "No database connection available; this check needs a live DB to verify."))
        return out

    try:
        leaks = conn.execute(
            "SELECT mark_type, source, COUNT(*) FROM marks_official "
            "WHERE source IN ('BNP_BVAL','BBG_INTERP') GROUP BY mark_type, source").fetchall()
    except sqlite3.Error as exc:
        out.append(_row("marks_official never resolves to a reconciliation-only source", "fail",
                         f"Could not query marks_official ({exc})."))
        return out
    if leaks:
        detail = "; ".join(f"{n} {mt} row(s) sourced from {src}" for mt, src, n in leaks)
        out.append(_row("marks_official never resolves to a reconciliation-only source", "fail",
                         f"marks_official is returning reconciliation-only rows as if official: {detail}. "
                         "This should be structurally impossible given the view's CASE expression; "
                         "check the view definition has not been altered."))
    else:
        out.append(_row("marks_official never resolves to a reconciliation-only source", "pass",
                         "No BNP_BVAL or BBG_INTERP rows are being served as official marks."))
    return out


# --------------------------------------------------------------------------- 3/4. FX & futures coverage
def check_fx_and_future_coverage(conn: sqlite3.Connection, as_of: str) -> List[Check]:
    try:
        from data.bloomberg.inventory import mark_inventory
    except Exception as exc:
        return [_row("FX/futures marks coverage", "fail",
                     f"Could not load data.bloomberg.inventory ({exc.__class__.__name__}); "
                     "coverage cannot be checked.")]
    try:
        df = mark_inventory(conn, as_of)
    except Exception as exc:
        return [_row("FX/futures marks coverage", "fail",
                     f"mark_inventory raised {exc.__class__.__name__} for as_of={as_of}.")]

    out: List[Check] = []
    for mark_type, label in (("SPOT", "FX spot"), ("FWD_OUTRIGHT", "FX forward outright"),
                              ("FUTURE_PX", "Futures price")):
        sub = df[df["mark_type"] == mark_type] if not df.empty else df
        needed = len(sub)
        if needed == 0:
            out.append(_row(f"{label} coverage", "pass", f"No open positions need a {mark_type} mark today."))
            continue
        missing = sub[sub["status"] == "MISSING"]
        interp = sub[sub["status"] == "INTERP"]
        if not missing.empty:
            examples = ", ".join(f"{r.instrument_id} {r.settle_date}" for r in missing.itertuples())
            out.append(_row(f"{label} coverage", "fail",
                             f"{len(missing)} of {needed} required {mark_type} marks are missing entirely "
                             f"for {as_of} (e.g. {examples}) -- the Bloomberg pull may have failed or the "
                             "instrument wasn't requested."))
        elif mark_type == "FWD_OUTRIGHT" and not interp.empty:
            examples = ", ".join(f"{r.instrument_id} {r.settle_date}" for r in interp.itertuples())
            out.append(_row(f"{label} coverage", "warning",
                             f"{len(interp)} of {needed} forward outrights are only available via BBG_INTERP "
                             f"fallback interpolation, not the direct broken-date quote (e.g. {examples})."))
        else:
            out.append(_row(f"{label} coverage", "pass",
                             f"All {needed} required {mark_type} marks are present as official for {as_of}."))
    return out


# --------------------------------------------------------------------------- 5. IRS / OIS curves
_OIS_INDEX = {"USD": "SOFR", "EUR": "ESTR", "GBP": "SONIA", "JPY": "TONA",
              "CHF": "SARON", "CAD": "CORRA", "AUD": "AONIA"}


def check_irs_curve_coverage(conn: sqlite3.Connection, as_of: str) -> List[Check]:
    out: List[Check] = []
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    except sqlite3.Error as exc:
        return [_row("IRS / OIS curve coverage", "fail", f"Could not read the schema ({exc}).")]

    if "trades" not in tables:
        return [_row("IRS / OIS curve coverage", "warning", "No trades table found; skipping.")]

    try:
        irs_ccys = {r[0] for r in conn.execute(
            "SELECT DISTINCT i.base_ccy FROM trades t JOIN instruments i USING (instrument_id) "
            "WHERE t.product = 'IRS'")}
    except sqlite3.Error as exc:
        return [_row("IRS / OIS curve coverage", "fail", f"Could not read IRS trades ({exc}).")]

    if not irs_ccys:
        out.append(_row("IRS / OIS curve coverage", "pass", "No live IRS trades; no curve is required today."))
        return out

    if "curve_quotes" not in tables:
        out.append(_row("IRS / OIS curve coverage", "fail",
                         "curve_quotes table does not exist, but live IRS trades exist that need an OIS curve."))
        return out

    missing_idx = [c for c in irs_ccys if c not in _OIS_INDEX]
    if missing_idx:
        out.append(_row("IRS / OIS curve coverage", "warning",
                         f"IRS trades exist in {', '.join(sorted(missing_idx))}, which has no OIS index mapped "
                         "in Phase 1 scope (USD/EUR/GBP/JPY/CHF/CAD/AUD only)."))

    for ccy in sorted(irs_ccys & _OIS_INDEX.keys()):
        idx = _OIS_INDEX[ccy]
        n = conn.execute("SELECT COUNT(*) FROM curve_quotes WHERE as_of_date = ? AND ccy = ? AND \"index\" = ?",
                          (as_of, ccy, idx)).fetchone()[0]
        if n == 0:
            out.append(_row(f"{ccy}-{idx} curve quotes for {as_of}", "fail",
                             f"No {idx} curve quotes found for {as_of} in curve_quotes, but a live {ccy} IRS "
                             "trade needs one -- the Bloomberg curve pull may not have run."))
        else:
            out.append(_row(f"{ccy}-{idx} curve quotes for {as_of}", "pass",
                             f"{n} {idx} curve quote(s) present for {as_of}."))

    for mark_type in ("PAR_RATE", "PV_USD", "DV01_USD"):
        bad = conn.execute(
            "SELECT COUNT(*) FROM marks WHERE mark_type = ? AND source = 'BBG_BDH' AND as_of_date = ? "
            "AND instrument_id IN (SELECT instrument_id FROM marks_official WHERE mark_type = ? AND source = 'BBG_BDH')",
            (mark_type, as_of, mark_type)).fetchone()[0]
        official_source = conn.execute(
            "SELECT DISTINCT source FROM marks_official WHERE mark_type = ? AND as_of_date = ?",
            (mark_type, as_of)).fetchall()
        wrong = [s for (s,) in official_source if s != "QL_PRICER"]
        if wrong:
            out.append(_row(f"{mark_type} official source", "fail",
                             f"marks_official resolves {mark_type} to {wrong} for {as_of}, not QL_PRICER; "
                             "BBG_BDH (Bloomberg SWPM) is reconciliation-only for IRS per the 2026-09-15 decision."))
        elif official_source:
            out.append(_row(f"{mark_type} official source", "pass",
                             f"{mark_type} resolves to QL_PRICER for {as_of}, as required."))
    return out


# --------------------------------------------------------------------------- 5b. FX options
def check_option_coverage(conn: sqlite3.Connection, as_of: str) -> List[Check]:
    """Every open FX option (expiry on or after as_of) must have an official PREMIUM and
    DELTA mark for as_of. An option whose terms are incomplete (strike 0 in
    instrument_options) can never be priced and is reported separately, because the fix
    is typing its terms in the Blotter's Options view, not a Bloomberg pull."""
    out: List[Check] = []
    try:
        rows = conn.execute(
            "SELECT t.trade_id, t.instrument_id, i.expiry_date, COALESCE(o.strike, 0), COALESCE(o.payoff, 'VANILLA') "
            "FROM trades_official t JOIN instruments i USING (instrument_id) "
            "LEFT JOIN instrument_options o USING (instrument_id) "
            "WHERE t.product = 'FX_OPTION' AND i.expiry_date >= ?", (as_of,)).fetchall()
    except sqlite3.Error as exc:
        return [_row("FX option marks coverage", "fail", f"Could not read option trades ({exc}).")]
    if not rows:
        return [_row("FX option marks coverage", "pass", "No open FX options; no option marks are required today.")]

    no_terms = [inst for _, inst, _, strike, payoff in rows
                if strike == 0 and payoff in ("VANILLA", "DIGITAL", "AMERICAN", "ASIAN", "BARRIER_KI", "BARRIER_KO")]
    if no_terms:
        out.append(_row("FX option terms on file", "warning",
                         f"{len(no_terms)} open option(s) have no strike on file, so they cannot be priced until "
                         f"their terms are entered in the Blotter's Options view: {', '.join(sorted(set(no_terms)))}."))
    else:
        out.append(_row("FX option terms on file", "pass", f"All {len(rows)} open option(s) carry a strike and payoff type."))

    priceable = sorted({inst for _, inst, _, strike, _ in rows if strike != 0})
    for mark_type in ("PREMIUM", "DELTA"):
        if not priceable:
            continue
        have = {r[0] for r in conn.execute(
            "SELECT DISTINCT instrument_id FROM marks_official WHERE mark_type = ? AND as_of_date = ?",
            (mark_type, as_of))}
        missing = [inst for inst in priceable if inst not in have]
        if missing:
            out.append(_row(f"FX option {mark_type} coverage", "fail",
                             f"{len(missing)} of {len(priceable)} priceable open option(s) have no official {mark_type} "
                             f"for {as_of} (e.g. {', '.join(missing[:4])}) -- the options step of the live feed "
                             "did not price them; check the vol surface and the OIS curves for their currencies."))
        else:
            out.append(_row(f"FX option {mark_type} coverage", "pass",
                             f"All {len(priceable)} priceable open option(s) have an official {mark_type} for {as_of}."))
    return out


# --------------------------------------------------------------------------- 5c. index fixings
def check_index_fixings(conn: sqlite3.Connection, as_of: str) -> List[Check]:
    """A seasoned swap (effective date already passed) needs the overnight fixings of its
    index from its effective date to as_of to value the current float period. Reports,
    per index, whether any fixings are on file over that window."""
    out: List[Check] = []
    try:
        rows = conn.execute(
            "SELECT i.base_ccy, MIN(l.start_date) FROM trades_official t "
            "JOIN instruments i USING (instrument_id) JOIN trade_legs l USING (trade_id) "
            "WHERE t.product = 'IRS' AND l.leg_type = 'FLOAT' AND l.start_date <= ? AND l.settle_date >= ? "
            "GROUP BY i.base_ccy", (as_of, as_of)).fetchall()
    except sqlite3.Error as exc:
        return [_row("Overnight index fixings", "fail", f"Could not read IRS legs ({exc}).")]
    if not rows:
        return [_row("Overnight index fixings", "pass", "No seasoned IRS trades; no fixings are required today.")]
    for ccy, first_start in rows:
        idx = _OIS_INDEX.get(ccy)
        if idx is None:
            out.append(_row(f"{ccy} overnight fixings", "warning", f"{ccy} has no OIS index in scope; fixings cannot be checked."))
            continue
        n, last = conn.execute(
            'SELECT COUNT(*), MAX(fixing_date) FROM index_fixings WHERE "index" = ? AND fixing_date BETWEEN ? AND ?',
            (idx, first_start, as_of)).fetchone()
        if n == 0:
            out.append(_row(f"{idx} fixings since {first_start}", "fail",
                             f"No {idx} fixings on file between {first_start} and {as_of}, but a seasoned {ccy} swap "
                             "needs them to value its current float period -- the rates step of the live feed writes "
                             "them (data/bloomberg/rates_marketdata.py::write_fixings)."))
        else:
            out.append(_row(f"{idx} fixings since {first_start}", "pass", f"{n} {idx} fixing(s) on file, latest {last}."))
    return out


# --------------------------------------------------------------------------- 5d. unverified assumptions
def check_unverified_assumptions() -> List[Check]:
    """The FX vol feed (data/bloomberg/vol_marketdata.py) was written without Terminal
    access and lists every ticker / field / request-shape guess as 'UNVERIFIED' in its
    docstring. Surface that count here so the person on the Bloomberg PC knows the option
    Greeks rest on guesses until each is ticked off."""
    try:
        import data.bloomberg.vol_marketdata as vm
        doc = vm.__doc__ or ""
    except Exception as exc:
        return [_row("FX vol ticker assumptions", "fail", f"Could not import data.bloomberg.vol_marketdata ({exc.__class__.__name__}).")]
    items = [ln.strip() for ln in doc.splitlines() if "UNVERIFIED --" in ln]
    if not items:
        return [_row("FX vol ticker assumptions", "pass", "No unverified ticker assumptions remain in the vol feed.")]
    return [_row("FX vol ticker assumptions", "warning",
                 f"{len(items)} FX vol ticker/field assumptions are still marked UNVERIFIED in "
                 "data/bloomberg/vol_marketdata.py (ATM/RR/BF ticker shapes, PX_LAST field, 'ON' tenor, "
                 "request type, vol-point scale). Run  py -3 -m data.bloomberg.vol_marketdata --probe  "
                 "on the Bloomberg PC and tick each one off; option Greeks are only as good as these.")]


# --------------------------------------------------------------------------- 5e. clock
def check_clock(as_of: str) -> List[Check]:
    """The feed stamps marks with today's New York date. A PC clock or zone that is off,
    or an as-of date that is not today, is the usual reason 'everything is missing'."""
    try:
        from zoneinfo import ZoneInfo
        now_ny = datetime.now(ZoneInfo("America/New_York"))
    except Exception as exc:
        return [_row("PC clock / New York date", "fail",
                     f"Cannot resolve America/New_York ({exc.__class__.__name__}); tzdata may be missing -- "
                     "run  py -3 2_launcher.py setup.")]
    today_ny = now_ny.date().isoformat()
    local = datetime.now().astimezone()
    detail = (f"Local time {local.strftime('%Y-%m-%d %H:%M %Z')} = New York {now_ny.strftime('%Y-%m-%d %H:%M')}; "
              f"marks pulled now are stamped {today_ny}.")
    if as_of != today_ny:
        return [_row("PC clock / New York date", "warning",
                     detail + f" The checks above ran for as-of {as_of}, which is not today's New York date; "
                              "a Ladder or Market data date picker left on an old date finds no official marks.")]
    return [_row("PC clock / New York date", "pass", detail)]


# --------------------------------------------------------------------------- 6. snapped_at offset
def check_snapped_at_offset(conn: sqlite3.Connection, as_of: str) -> List[Check]:
    try:
        rows = conn.execute(
            "SELECT DISTINCT snapped_at FROM marks_official WHERE as_of_date = ?", (as_of,)).fetchall()
    except sqlite3.Error as exc:
        return [_row("snapped_at carries a resolved offset", "fail", f"Could not query marks_official ({exc}).")]
    if not rows:
        return [_row("snapped_at carries a resolved offset", "warning",
                     f"No official marks recorded for {as_of} yet; nothing to check.")]
    naive = []
    for (snapped,) in rows:
        try:
            ts = datetime.fromisoformat(snapped)
        except (TypeError, ValueError):
            naive.append(snapped)
            continue
        if ts.tzinfo is None:
            naive.append(snapped)
    if naive:
        examples = ", ".join(str(n) for n in naive[:5])
        return [_row("snapped_at carries a resolved offset", "fail",
                     f"{len(naive)} of {len(rows)} distinct snapped_at value(s) for {as_of} have no UTC offset "
                     f"(e.g. {examples}) -- CLAUDE.md requires the America/New_York offset resolved per row.")]
    return [_row("snapped_at carries a resolved offset", "pass",
                 f"All {len(rows)} distinct snapped_at value(s) for {as_of} carry a resolved UTC offset.")]


# --------------------------------------------------------------------------- 7. last live feed pull
def check_last_pull(db_path: Optional[Path]) -> List[Check]:
    if db_path is None:
        return [_row("Last marks pull", "warning", "No database path resolved; cannot read the feed status file.")]
    try:
        from data.bloomberg.live import read_status
    except Exception as exc:
        return [_row("Last marks pull", "warning", f"Could not load data.bloomberg.live ({exc.__class__.__name__}).")]
    try:
        status = read_status(db_path)
    except Exception as exc:
        return [_row("Last marks pull", "warning", f"Could not read the feed status file ({exc.__class__.__name__}).")]
    if status is None:
        return [_row("Last marks pull", "warning", "No Bloomberg pull has run yet on this database.")]
    if status.get("connected") and not status.get("failed"):
        return [_row("Last marks pull", "pass",
                     f"Last pull at {status.get('time', 'an unknown time')} wrote "
                     f"{status.get('written', 0)} of {status.get('requested', 0)} requested marks.")]
    if status.get("connected"):
        return [_row("Last marks pull", "warning",
                     f"Last pull connected but {status.get('failed', 0)} of {status.get('requested', 0)} "
                     "requested marks failed.")]
    return [_row("Last marks pull", "fail", f"Last pull did not connect: {status.get('reason', 'unknown reason')}.")]


# --------------------------------------------------------------------------- orchestration
def run_bloomberg_diagnostics(db_path: Optional[str] = None, as_of: Optional[str] = None,
                              host: Optional[str] = None, port: Optional[int] = None) -> List[Check]:
    """Runs every check and returns a flat list of {name, status, message} dicts, newest
    to CLAUDE.md's own ordering (session, official-source mapping, FX/futures coverage,
    IRS/curve coverage, snapped_at, last pull). Never raises: any single check that blows
    up is caught and turned into one 'fail' row for that check rather than aborting the
    rest, since a partial report is far more useful than none on a page a non-technical
    user clicks."""
    host = host or os.environ.get("BLP_HOST", "localhost")
    port = int(port or os.environ.get("BLP_PORT", "8194"))
    resolved_db = Path(db_path) if db_path else _default_db_path()

    results: List[Check] = []

    def _safe(label: str, fn, *args):
        try:
            results.extend(fn(*args))
        except Exception as exc:
            results.append(_row(label, "fail", f"Check crashed unexpectedly ({exc.__class__.__name__}); "
                                                "see server logs for detail."))

    _safe("Bloomberg session connectivity", check_session, host, port)

    conn: Optional[sqlite3.Connection] = None
    if resolved_db is not None and Path(resolved_db).exists():
        try:
            conn = sqlite3.connect(f"file:{Path(resolved_db).as_posix()}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            results.append(_row("Database connectivity", "fail", f"Could not open {resolved_db} read-only ({exc})."))
    else:
        results.append(_row("Database connectivity", "warning",
                             f"No database found at {resolved_db}; downstream checks are skipped."))

    _safe("Official-source mapping", check_official_source_mapping, conn)

    if conn is not None:
        resolved_as_of = as_of or _latest_as_of(conn)
        _safe("FX/futures marks coverage", check_fx_and_future_coverage, conn, resolved_as_of)
        _safe("IRS / OIS curve coverage", check_irs_curve_coverage, conn, resolved_as_of)
        _safe("Overnight index fixings", check_index_fixings, conn, resolved_as_of)
        _safe("FX option marks coverage", check_option_coverage, conn, resolved_as_of)
        _safe("snapped_at carries a resolved offset", check_snapped_at_offset, conn, resolved_as_of)
        conn.close()
        _safe("PC clock / New York date", check_clock, resolved_as_of)

    _safe("FX vol ticker assumptions", check_unverified_assumptions)

    _safe("Last marks pull", check_last_pull, resolved_db)

    return results


# --------------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=None, help="SQLite database (default: ui.app.get_db_path() or $RISK_DB)")
    p.add_argument("--as-of", default=None, help="as-of date (default: today in New York)")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--json", action="store_true", help="print raw JSON instead of a plain-text report")
    args = p.parse_args(argv)

    try:
        checks = run_bloomberg_diagnostics(db_path=args.db, as_of=args.as_of, host=args.host, port=args.port)
    except Exception:
        # Belt-and-braces: run_bloomberg_diagnostics() already catches per-check, but the
        # CLI must never dump a raw traceback to a non-technical reader either.
        print("Could not run Bloomberg diagnostics.", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(checks, indent=2))
    else:
        width = max((len(c["name"]) for c in checks), default=0)
        for c in checks:
            print(f"[{c['status'].upper():7s}] {c['name']:{width}s}  {c['message']}")
    return 0 if all(c["status"] != "fail" for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
