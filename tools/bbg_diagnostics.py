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
  2. Official-source mapping   -- schema.OFFICIAL_MARK_SOURCE / OFFICIAL_FALLBACK_SOURCE
                                   match CLAUDE.md's table exactly, and marks_official
                                   never serves BNP_BVAL, nor BBG_INTERP outside its one
                                   official role (the FWD_OUTRIGHT fallback, user decision
                                   2026-09-18), nor BBG_BDH for the three IRS mark_types
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
                                   (the close is 15:00 America/New_York, user decision
                                   2026-09-21), not a naive timestamp
  7. Last live feed pull       -- data.bloomberg.live's last recorded status
  7b. Past closes (backfill)   -- from the status file's "backfill" block: which Bloomberg
                                   field gave the forward-points divisor (FWD_POINTS_SCALE
                                   or FWD_SCALE) or that neither did, and whether past days
                                   are being asked for again at the 15:00 New York close
  8. Unverified assumptions    -- whether each FX vol ticker/field guess in
                                   data/bloomberg/vol_marketdata.py has been confirmed by
                                   a real response on a live pull (vol_ticker_checks),
                                   not just counted as still UNVERIFIED in the docstring
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
    (The `positions` table -- BNP-fed, and dropped from the schema entirely 2026-09-17 --
    was never used as the default here for the same reason: it would have pointed every
    coverage check at a fixed stale snapshot date forever.)"""
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
# CLAUDE.md "Official marks", 2026-09-18: BBG_INTERP is official ONLY as the FWD_OUTRIGHT
# fallback where no direct BBG_BFXFORWARD quote exists for the same key.
_EXPECTED_FALLBACK = {"FWD_OUTRIGHT": "BBG_INTERP"}


def check_official_source_mapping(conn: Optional[sqlite3.Connection]) -> List[Check]:
    out: List[Check] = []
    try:
        from data.ingest.schema import OFFICIAL_MARK_SOURCE, OFFICIAL_FALLBACK_SOURCE
    except Exception as exc:
        return [_row("Official-source mapping", "fail",
                     f"Could not import data.ingest.schema.OFFICIAL_MARK_SOURCE ({exc.__class__.__name__}).")]

    mismatches = [f"{mt}: schema says {OFFICIAL_MARK_SOURCE.get(mt)!r}, CLAUDE.md says {src!r}"
                  for mt, src in _EXPECTED_OFFICIAL.items() if OFFICIAL_MARK_SOURCE.get(mt) != src]
    if dict(OFFICIAL_FALLBACK_SOURCE) != _EXPECTED_FALLBACK:
        mismatches.append(f"fallback: schema says {dict(OFFICIAL_FALLBACK_SOURCE)!r}, "
                          f"CLAUDE.md says {_EXPECTED_FALLBACK!r}")
    bad_official = {mt: src for mt, src in OFFICIAL_MARK_SOURCE.items() if src in _NEVER_OFFICIAL}
    if mismatches or bad_official:
        detail = "; ".join(mismatches + [f"{mt} wrongly points at {src} (reconciliation-only)"
                                          for mt, src in bad_official.items()])
        out.append(_row("Official-source mapping matches CLAUDE.md", "fail",
                         f"OFFICIAL_MARK_SOURCE has drifted from the contract: {detail}."))
    else:
        out.append(_row("Official-source mapping matches CLAUDE.md", "pass",
                         "SPOT/FWD_OUTRIGHT->BBG_BFXFORWARD (FWD_OUTRIGHT falls back to BBG_INTERP where "
                         "Bloomberg has no direct quote for that date), FUTURE_PX->BBG_BDH, "
                         "PAR_RATE/PV_USD/DV01_USD/CASHFLOW_USD->QL_PRICER, "
                         "PREMIUM/DELTA/Greeks->QL_OPTIONS_PRICER, as specified."))

    if conn is None:
        out.append(_row("marks_official never resolves to a reconciliation-only source", "warning",
                         "No database connection available; this check needs a live DB to verify."))
        return out

    try:
        leaks = conn.execute(
            "SELECT mark_type, source, COUNT(*) FROM marks_official "
            "WHERE source = 'BNP_BVAL' OR (source = 'BBG_INTERP' AND mark_type != 'FWD_OUTRIGHT') "
            "GROUP BY mark_type, source").fetchall()
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
                         "No BNP_BVAL row, and no BBG_INTERP row outside its FWD_OUTRIGHT fallback role, "
                         "is served as official."))
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
                             f"{len(interp)} of {needed} forward outrights fall on broken dates between Bloomberg's "
                             f"standard tenors and are interpolated (BBG_INTERP, never official) between the pair's "
                             f"own FWD_CURVE points (e.g. {examples}). The API exposes no direct broken-date "
                             "outright (docs/open-questions.md item 27); the standard-tenor points themselves are "
                             "stored as official FWD_OUTRIGHT marks at their own dates."))
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
def _last_option_skip_reasons(db_path: Optional[Path]) -> Dict[str, str]:
    """{trade_id: reason} from the last recorded pull's status["options"]["skipped"]
    (data.bloomberg.live._options_step's own per-trade list -- a plain string there means
    the whole step was skipped, e.g. "no FX_OPTION trades to price", not a per-trade
    reason, and is ignored here). Never raises: any failure to read returns {}."""
    if db_path is None:
        return {}
    try:
        from data.bloomberg.live import read_status
        status = read_status(db_path)
    except Exception:
        return {}
    if not status:
        return {}
    skipped = status.get("options", {}).get("skipped")
    if not isinstance(skipped, list):
        return {}
    return {s["trade_id"]: s.get("reason", "") for s in skipped if isinstance(s, dict) and s.get("trade_id")}


def check_option_coverage(conn: sqlite3.Connection, as_of: str, db_path: Optional[Path] = None) -> List[Check]:
    """Every open FX option (expiry on or after as_of) must have an official PREMIUM and
    DELTA mark for as_of. An option whose terms are incomplete (strike 0 in
    instrument_options) can never be priced and is reported separately, because the fix
    is typing its terms in the Blotter's Options view, not a Bloomberg pull.

    Item 3a (2026-09-17): a missing-mark FAIL used to say only "N of M ... did not price
    them; check the vol surface and the OIS curves" -- forcing the user to go read the raw
    status JSON to find out *why* a specific option was skipped. It now looks up the last
    recorded pull's per-trade skip reason (data.bloomberg.live._options_step's own
    diagnosis, e.g. "no curve/rate SEK" or a vol-step ticker failure) via `db_path` and
    prints it verbatim next to the instrument."""
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

    trade_ids_by_instrument: Dict[str, List[str]] = {}
    for tid, inst, _expiry, _strike, _payoff in rows:
        trade_ids_by_instrument.setdefault(inst, []).append(tid)

    no_terms = [inst for _, inst, _, strike, payoff in rows
                if strike == 0 and payoff in ("VANILLA", "DIGITAL", "AMERICAN", "ASIAN", "BARRIER_KI", "BARRIER_KO")]
    if no_terms:
        out.append(_row("FX option terms on file", "warning",
                         f"{len(no_terms)} open option(s) have no strike on file, so they cannot be priced until "
                         f"their terms are entered in the Blotter's Options view: {', '.join(sorted(set(no_terms)))}."))
    else:
        out.append(_row("FX option terms on file", "pass", f"All {len(rows)} open option(s) carry a strike and payoff type."))

    skip_reasons = _last_option_skip_reasons(db_path)
    priceable = sorted({inst for _, inst, _, strike, _ in rows if strike != 0})
    for mark_type in ("PREMIUM", "DELTA"):
        if not priceable:
            continue
        have = {r[0] for r in conn.execute(
            "SELECT DISTINCT instrument_id FROM marks_official WHERE mark_type = ? AND as_of_date = ?",
            (mark_type, as_of))}
        missing = [inst for inst in priceable if inst not in have]
        if missing:
            message = (f"{len(missing)} of {len(priceable)} priceable open option(s) have no official {mark_type} "
                      f"for {as_of} (e.g. {', '.join(missing[:4])}) -- the options step of the live feed "
                      "did not price them; check the vol surface and the OIS curves for their currencies.")
            reasons = []
            for inst in missing:
                for tid in trade_ids_by_instrument.get(inst, []):
                    reason = skip_reasons.get(tid)
                    if reason:
                        reasons.append(f"{inst} ({tid}): {reason}")
            if reasons:
                shown = "; ".join(reasons[:6])
                more = f" (+{len(reasons) - 6} more)" if len(reasons) > 6 else ""
                message += f" Last pull's reported reason(s): {shown}{more}"
            elif skip_reasons or db_path is not None:
                message += " (the last pull's status file has no per-trade reason recorded for these -- press Pull now and re-check)."
            out.append(_row(f"FX option {mark_type} coverage", "fail", message))
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
def check_unverified_assumptions(db_path: Optional[Path]) -> List[Check]:
    """The FX vol feed (data/bloomberg/vol_marketdata.py) was written without Terminal
    access and lists every ticker / field / request-shape guess as 'UNVERIFIED' in its
    docstring. Since 2026-09-17, data/bloomberg/live.py's `_vol_step` requests every one
    of those tickers on every live pull with an open FX_OPTION in the book, and
    (2026-09-18) `vol_marketdata.record_vol_ticker_checks` persists what Bloomberg
    actually returned for each assumption into the `vol_ticker_checks` table -- so the
    live pull itself is now the probe, and this check reads that record instead of
    unconditionally telling the user to run --probe by hand.

    PASS once every assumption has been confirmed (outcome "OK") by a real Bloomberg
    response; FAIL naming the assumption and the exact ticker/field Bloomberg rejected
    (SECURITY_ERROR / FIELD_EXCEPTION / a value outside the plausible vol-points range);
    WARNING "not yet exercised" when no live pull with options in the book has ever
    written anything to vol_ticker_checks (nothing to report on yet), or when only some
    assumptions have evidence so far."""
    try:
        import data.bloomberg.vol_marketdata as vm
    except Exception as exc:
        return [_row("FX vol ticker assumptions", "fail",
                     f"Could not import data.bloomberg.vol_marketdata ({exc.__class__.__name__}).")]

    if db_path is None or not Path(db_path).exists():
        return [_row("FX vol ticker assumptions", "warning",
                     "not yet exercised: no live pull with options in the book has run "
                     "(no database found to read the vol_ticker_checks record from).")]
    try:
        conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return [_row("FX vol ticker assumptions", "warning", f"Could not open {db_path} read-only ({exc}).")]
    try:
        checked = vm.read_vol_ticker_checks(conn)
    finally:
        conn.close()

    if not checked:
        return [_row("FX vol ticker assumptions", "warning",
                     "not yet exercised: no live pull with options in the book has run.")]

    expected = vm.ALL_ASSUMPTION_IDS
    failed = {aid: c for aid, c in checked.items() if c["outcome"] != "OK"}
    not_yet = [aid for aid in expected if aid not in checked]

    if failed:
        parts = [f"{aid} ({c['description']}): {c['detail']}" for aid, c in sorted(failed.items())]
        last_checked = next(iter(failed.values()))["last_checked"]
        return [_row("FX vol ticker assumptions", "fail",
                     f"{len(failed)} of {len(expected)} FX vol ticker/field assumptions were rejected by "
                     f"Bloomberg on the last live pull ({last_checked}): " + "; ".join(parts))]

    if not_yet:
        return [_row("FX vol ticker assumptions", "warning",
                     f"{len(checked)} of {len(expected)} FX vol ticker/field assumptions confirmed by a live "
                     f"pull so far; not yet exercised (no evidence yet): {', '.join(sorted(not_yet))}.")]

    last_checked = next(iter(checked.values()))["last_checked"]
    return [_row("FX vol ticker assumptions", "pass",
                 f"All {len(expected)} FX vol ticker/field assumptions (ATM/RR/BF ticker shapes, PX_LAST "
                 f"field, 'ON' tenor, request type, vol-point scale) were confirmed by a real Bloomberg "
                 f"response on the last live pull with options in the book ({last_checked}).")]


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
def check_last_pull(db_path: Optional[Path], as_of: Optional[str] = None) -> List[Check]:
    """PASS only when the last recorded pull is trustworthy *now*. The feed's first pull
    runs the instant the app starts, usually before any blotter is uploaded, and honestly
    records `requested: 0`; until the next cycle that stale status must not read as PASS
    while the book needs marks (Bloomberg PC, 2026-09-17). The freshness test itself is
    data.bloomberg.inventory.stale_empty_pull_reason, shared with ui/tabs/market_data.py."""
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
        stale_reason = None
        try:
            from data.bloomberg.inventory import stale_empty_pull_reason
            resolved_as_of = as_of or status.get("as_of_date") or date.today().isoformat()
            conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
            try:
                stale_reason = stale_empty_pull_reason(conn, status, resolved_as_of)
            finally:
                conn.close()
        except Exception:
            stale_reason = None  # cannot verify freshness right now; report the plain status below
        if stale_reason:
            return [_row("Last marks pull", "fail",
                         f"Last pull at {status.get('time', 'an unknown time')} looks stale: {stale_reason}")]
        curve_points = status.get("curve_points_written") or 0
        extra = f" plus {curve_points} standard-tenor forward curve points" if curve_points else ""
        return [_row("Last marks pull", "pass",
                     f"Last pull at {status.get('time', 'an unknown time')} wrote "
                     f"{status.get('written', 0)} of {status.get('requested', 0)} requested marks{extra}.")]
    if status.get("connected"):
        # Item 3a (2026-09-17): "2 of 27 failed" alone forces the user to open the Market
        # data tab's own table to find out which -- list the failed instrument + detail
        # right here, so the diagnostics panel is self-contained.
        failed_items = [i for i in status.get("items", []) if i.get("status") == "FAILED"]
        message = (f"Last pull connected but {status.get('failed', 0)} of {status.get('requested', 0)} "
                  "requested marks failed.")
        if failed_items:
            shown = "; ".join(
                f"{i.get('instrument_id', '?')} {i.get('mark_type', '?')} {i.get('settle_date', '')}: "
                f"{i.get('detail') or 'no detail recorded'}"
                for i in failed_items[:6])
            more = f" (+{len(failed_items) - 6} more)" if len(failed_items) > 6 else ""
            message += " " + shown + more
        return [_row("Last marks pull", "warning", message)]
    return [_row("Last marks pull", "fail", f"Last pull did not connect: {status.get('reason', 'unknown reason')}.")]


# --------------------------------------------------------------------------- 7b. past closes (backfill block)
def check_backfill_report(db_path: Optional[Path]) -> List[Check]:
    """What the last backfill recorded in the status file's "backfill" block (2026-09-21),
    read-only: `points_scale` -- per pair, which Bloomberg field gave the forward-points
    divisor (FWD_POINTS_SCALE as is, or 10 ** FWD_SCALE), or that neither answered, with
    Bloomberg's own message -- and `note`, the sentence saying past days hold marks that
    are not the 15:00 New York close and are being asked for again. No row when there is
    nothing to say (no status file, or a backfill that needed no divisor and has no note)."""
    if db_path is None:
        return []
    try:
        from data.bloomberg.live import read_status
        block = (read_status(db_path) or {}).get("backfill") or {}
    except Exception:
        return []
    out: List[Check] = []
    scales = block.get("points_scale") or {}
    if scales:
        without = {pair: r for pair, r in scales.items() if not r.get("divisor")}
        if without:
            said = "; ".join(
                f"{pair}: " + ", ".join(f"{field}: {(r.get('errors') or {}).get(field) or (r.get('raw') or {}).get(field, 'not returned')}"
                                        for field in ("FWD_POINTS_SCALE", "FWD_SCALE"))
                for pair, r in sorted(without.items())[:6])
            out.append(_row("Forward points divisor", "fail",
                            f"{len(without)} of {len(scales)} pair(s) got no points divisor from FWD_POINTS_SCALE or "
                            f"FWD_SCALE on the last backfill, so their past-close forwards cannot be built ({said})."))
        else:
            fields = sorted({r.get("field") for r in scales.values()})
            pair, r = sorted(scales.items())[0]
            out.append(_row("Forward points divisor", "pass",
                            f"{' / '.join(fields)} answered for all {len(scales)} pair(s) on the last backfill "
                            f"(e.g. {pair}: {r.get('field')} = {(r.get('raw') or {}).get(r.get('field'))!r}, "
                            f"divisor {r.get('divisor'):g})."))
    if block.get("note"):
        out.append(_row("Past closes at 15:00 New York", "warning", str(block["note"])))
    return out


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
    resolved_as_of = as_of
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
        _safe("FX option marks coverage", check_option_coverage, conn, resolved_as_of, resolved_db)
        _safe("snapped_at carries a resolved offset", check_snapped_at_offset, conn, resolved_as_of)
        conn.close()
        _safe("PC clock / New York date", check_clock, resolved_as_of)

    _safe("FX vol ticker assumptions", check_unverified_assumptions, resolved_db)

    _safe("Last marks pull", check_last_pull, resolved_db, resolved_as_of)
    _safe("Past closes (backfill)", check_backfill_report, resolved_db)

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
