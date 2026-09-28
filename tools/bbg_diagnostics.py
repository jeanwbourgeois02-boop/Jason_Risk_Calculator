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

Severity rule for every mark-coverage row (user steer, 2026-09-28: "never scarier than the
Book"): the book is valued ONCE per run through the screens' own reader
(ui/tabs/blotter_pricing.py::priced_value_book, i.e. value_book plus the fill from the last
earlier close, at most 5 business days back), read-only, and each open trade that needs the
marks a row is about is classified by its outcome: PASS valued on an official mark of the
as-of date; WARNING valued but estimated from the near marks (mark source 'INTERP:') or
filled from an earlier close (the note "no price on <date>: value of the <earlier> close"),
the trades named and the app's own sentence quoted; FAIL only when the screen shows a dash,
with the reader's own reason. A missing mark is still named (it is what the pull did not
get), but it fails a row only when a trade shows no value. Greeks and an option's
underlying price are WARNING at worst (not P&L); unverified roots, unstored contract dates
and a not-exercised last pull are WARNING; a future with no Bloomberg ticker is FAIL only
when it also has no value on screen.

Checks (each becomes one or more result rows):
  1. Session connectivity      -- blpapi import + TCP + Session.start + //blp/refdata
  2. Official-source mapping   -- schema.OFFICIAL_MARK_SOURCE / OFFICIAL_FALLBACK_SOURCE
                                   match CLAUDE.md's table exactly, and marks_official
                                   never serves BNP_BVAL, nor BBG_INTERP outside its one
                                   official role (the FWD_OUTRIGHT fallback, user decision
                                   2026-09-18)
  3. FX marks coverage         -- every open FX pair/leg's SPOT and FWD_OUTRIGHT is
                                   OFFICIAL (not MISSING/INTERP/MANUAL fallback) as of
                                   the given date, via data.bloomberg.inventory
  4. Futures marks coverage    -- every open future's FUTURE_PX is OFFICIAL
  4b. The commodity steps (2026-09-28, user-approved), each reusing the app's own record of
      what the book needs (data.bloomberg.library.needed_on with include_unrequestable) and
      its own "on file" rules, never a second definition:
      - Contract roots verified       -- config/contracts.csv roots with bbg_verified true
                                         against the total, and the open book's roots still
                                         unverified (fix: py 2_launcher.py bbg-check)
      - Futures with no Bloomberg ticker -- open futures / options on futures / LME metals the
                                         library marks not requestable (live.not_requestable_futures)
      - Contract dates stored         -- open contracts still on an estimated expiry, with no
                                         Bloomberg dates in contract_static
                                         (inventory.contract_dates_inventory)
      - Options on futures            -- per open CMDTY_OPTION: its own official FUTURE_PX
                                         (P&L: fail), its underlying's FUTURE_PX, an OIS curve
                                         for its discount currency (or the USD SOFR fallback),
                                         and official DELTA/GAMMA/THETA/VEGA (Greeks: warning)
      - LME curves / LME prompt outrights -- per LME root with an open forward: cash and 3M
                                         official (inventory.lme_curve_status), and each
                                         ticket's prompt on file (BBG_BFXFORWARD or BBG_INTERP
                                         within the pillars; else engine.lme.forward_at says
                                         whether it can still be read between them)
      - Conversion spots              -- every non-USD open future / option on a future has
                                         the official SPOT of its currency's USD pair
                                         (role CONVERSION)
  5. OIS curve coverage       -- curve_quotes has today's quotes for every OIS index in
                                   scope that an open FX option's pair needs (the
                                   options price off those discount curves)
  5c. FX option coverage       -- every open option has a strike on file (else it can
                                   never be priced) and an official PREMIUM and DELTA
  5d. Clock                    -- local time vs New York, and whether as_of is the book
                                   date (New York, rolled at 17:00: data.bloomberg.live.book_today)
  6. snapped_at offset         -- official marks carry a timezone-resolved snapped_at
                                   (the close is 15:00 America/New_York, user decision
                                   2026-09-21), not a naive timestamp
  7. Last live feed pull       -- data.bloomberg.live's last recorded status
  7b. Past closes (backfill)   -- from the status file's "backfill" block: which Bloomberg
                                   field gave the forward-points divisor (FWD_POINTS_SCALE
                                   or FWD_SCALE) or that neither did, and whether past days
                                   are being asked for again at the 15:00 New York close
  7c. Last pull: commodity steps -- the status file's "contract_dates" (live.contract_dates_summary),
                                   "lme" (live.lme_summary), "not_requestable" and the
                                   options-on-futures count of "options"; one warning
                                   "not yet exercised" while no pull has connected
  8. Unverified assumptions    -- whether each FX vol ticker/field guess in
                                   data/bloomberg/vol_marketdata.py has been confirmed by
                                   a real response on a live pull (vol_ticker_checks),
                                   not just counted as still UNVERIFIED in the docstring
"""
from __future__ import annotations

import argparse
import json
import os
import re
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
        from data.paths import get_db_path
        return get_db_path()
    except Exception:
        env = os.environ.get("RISK_DB")
        return Path(env) if env else None


def _today_ny(now: Optional[datetime] = None) -> str:
    """The book date the live feed stamps marks with: `data.bloomberg.live.book_today`, the
    app's one day boundary (the New York date until 17:00 New York, the next date from then
    on: user decision 2026-09-22, the day turns at 05:00 Hong Kong). Imported lazily so the
    tool still runs when the app's imports fail; then, as before, the New York calendar
    date, and the PC's date when the zone itself cannot be resolved. `now` is for tests: a
    tz-aware datetime, taken as the moment the check runs.
    (The `positions` table -- BNP-fed, and dropped from the schema entirely 2026-09-17 --
    was never used as the default here for the same reason: it would have pointed every
    coverage check at a fixed stale snapshot date forever.)"""
    try:
        from zoneinfo import ZoneInfo
        ny_now = (now if now is not None else datetime.now(ZoneInfo("America/New_York"))
                  ).astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return date.today().isoformat()
    try:
        from data.bloomberg.live import book_today
    except Exception:
        return ny_now.date().isoformat()
    return book_today(ny_now).isoformat()


def _latest_as_of(conn: sqlite3.Connection) -> str:
    return _today_ny()


# --------------------------------------------------------------------------- 0. what the screens show
_FILL_NOTE_RE = re.compile(r"no price on (\S+): value of the (\S+) close")


def _short_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(str(iso)[:10]).strftime("%d %b").lstrip("0")
    except (TypeError, ValueError):
        return str(iso)


class _Outcomes:
    """The book on `as_of` as every screen shows it (user steer, 2026-09-28: the diagnostics
    must agree with the Book, never be scarier): the shared reader
    `ui/tabs/blotter_pricing.py::priced_value_book` -- `value_book` plus the fill from the
    last earlier close, at most 5 business days back (`engine.pnl.reference.fill_book`) --
    run ONCE per diagnostics run, read-only, and each open trade classified by its outcome:

    - 'official'  valued on an official mark of the as-of date (PASS);
    - 'estimated' valued off the near marks, `mark_source` starting 'INTERP:' (WARNING);
    - 'filled'    valued from an earlier close, the note "no price on <date>: value of the
                  <earlier> close" (WARNING);
    - 'missing'   the screen shows a dash, with the reader's own `reason` (FAIL).

    Settled and closed-out trades are frozen and take no mark today: left out. `error` is
    set (and `rows` empty) when the book could not be valued; the checks then fall back to
    judging the marks alone and say so."""

    def __init__(self, conn: sqlite3.Connection, as_of: str):
        self.as_of = as_of
        self.rows: Dict[str, dict] = {}
        self.error: Optional[str] = None
        try:
            frame = self._read(conn, as_of)
        except Exception as exc:  # noqa: BLE001 -- the checks fall back to the marks alone
            self.error = f"{exc.__class__.__name__}: {exc}"
            return
        for r in frame.itertuples(index=False):
            if str(getattr(r, "status", "")) != "OPEN":
                continue
            reason = str(getattr(r, "reason", "") or "")
            note = str(getattr(r, "note", "") or "")
            source = str(getattr(r, "mark_source", "") or "")
            fill = _FILL_NOTE_RE.search(note)
            if reason:
                kind, text = "missing", reason
            elif fill:
                kind, text = "filled", note
            elif source.startswith("INTERP:"):
                kind, text = "estimated", source
            else:
                kind, text = "official", source
            self.rows[str(r.trade_id)] = {"trade_id": str(r.trade_id), "instrument_id": str(r.instrument_id),
                                          "product": str(r.product), "kind": kind, "text": text,
                                          "filled_from": fill.group(2) if fill else ""}

    @staticmethod
    def _read(conn: sqlite3.Connection, as_of: str):
        try:
            from ui.tabs.blotter_pricing import priced_value_book
            frame, _n_filled, _n_total = priced_value_book(conn, as_of)
            return frame
        except ImportError:
            pass
        from engine.pnl.reference import fill_book
        from engine.pnl.valuation import value_book
        frame = value_book(conn, as_of)
        frame, _filled = fill_book(frame, as_of, lambda iso, ids: value_book(conn, iso, trade_ids=ids))
        return frame

    def select(self, products=None, trade_ids=None, instrument_ids=None) -> List[dict]:
        """The open trades' outcomes narrowed by product, trade id or instrument id (any given
        filter must match)."""
        out = []
        for row in self.rows.values():
            if products is not None and row["product"] not in products:
                continue
            if trade_ids is not None and row["trade_id"] not in trade_ids:
                continue
            if instrument_ids is not None and row["instrument_id"] not in instrument_ids:
                continue
            out.append(row)
        return out


def _outcome_sentences(rows: List[dict], what: str) -> tuple:
    """(status, sentences) for a set of open trades as the screens value them: 'fail' when
    any shows no value, 'warning' when any is estimated or filled, else 'pass'; one sentence
    per kind, naming the trades and quoting the app's own wording."""
    total = len(rows)
    by_kind: Dict[str, List[dict]] = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r)
    sentences = []
    if by_kind.get("filled"):
        lst = by_kind["filled"]
        sentences.append(f"{len(lst)} of {total} {what} priced off the last close (thin contracts): "
                         + _names([f"{r['instrument_id']} ({_short_date(r['filled_from'])})" for r in lst], 4)
                         + f"; the app says \"{lst[0]['text']}\"")
    if by_kind.get("estimated"):
        lst = by_kind["estimated"]
        sentences.append(f"{len(lst)} of {total} {what} estimated from the near marks (hard rule 2): "
                         + _names([f"{r['instrument_id']} ({r['text']})" for r in lst], 3))
    if by_kind.get("missing"):
        lst = by_kind["missing"]
        sentences.append(f"{len(lst)} of {total} {what} show no value on screen: "
                         + _names([f"{r['instrument_id']} ({r['text']})" for r in lst], 3))
    status = "fail" if by_kind.get("missing") else ("warning" if by_kind.get("filled") or by_kind.get("estimated") else "pass")
    return status, sentences


def _screen_severity(outcomes: Optional["_Outcomes"], rows: List[dict], what: str, marks_short: bool) -> tuple:
    """The severity a mark-coverage row takes: what the screens show for the trades that
    need those marks (`_outcome_sentences`). With no readable book (`outcomes.error`), the
    marks alone decide: 'fail' when any is short, with a sentence saying the book could not
    be compared. `marks_short` says whether the mark table itself has a gap."""
    if outcomes is None or outcomes.error:
        why = outcomes.error if outcomes is not None else "no reader"
        return ("fail" if marks_short else "pass",
                [f"the book itself could not be valued to compare with the screens ({why})"] if marks_short else [])
    status, sentences = _outcome_sentences(rows, what)
    if marks_short and status == "pass" and not rows:
        # a mark short but no open trade of this kind reads it: nothing on screen is affected
        sentences.append(f"no open {what} read the missing mark today, so nothing on screen is affected")
    return status, sentences


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
    # reported instead of silently agreed with. DELTA/PREMIUM and the Greeks are the options
    # pricer's (QL_OPTIONS_PRICER); the swap mark types left with the rates book.
    "SPOT": "BBG_BFXFORWARD",
    "FWD_OUTRIGHT": "BBG_BFXFORWARD",
    "FUTURE_PX": "BBG_BDH",
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
_FX_LEG_PRODUCTS = ("FX_SPOT", "FX_FWD", "FX_SWAP")


def check_fx_and_future_coverage(conn: sqlite3.Connection, as_of: str,
                                 outcomes: Optional[_Outcomes] = None) -> List[Check]:
    """The marks the book needs today (data.bloomberg.inventory.mark_inventory: SPOT,
    FWD_OUTRIGHT, FUTURE_PX) against what is on file, with the row's SEVERITY taken from
    what the screens show for the trades that read them (user steer, 2026-09-28: a thin
    contract with no print is estimated from the near marks or filled from the last close,
    and the diagnostics must never be scarier than the Book). A missing mark is still named
    -- it is what the pull did not get -- but the row fails only when a trade shows a dash."""
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

    # 2026-09-28: an LME metal's cash SPOT and prompt FWD_OUTRIGHT are in the inventory too
    # (library rows of the LME product) but are not FX marks: the LME rows below
    # (check_lme_curves) judge them by the engine's own curve rule, so they leave the FX
    # labelled rows here rather than fail them with an FX-worded reason. Likewise an option
    # on a future's own price and its underlying's are the "Options on futures" rows'.
    try:
        from engine.lme import is_lme_instrument
    except Exception:  # noqa: BLE001 -- no LME layer: nothing to leave out
        is_lme_instrument = lambda _i: False          # noqa: E731
    if outcomes is None:
        outcomes = _Outcomes(conn, as_of)
    fx_trades = outcomes.select(products=_FX_LEG_PRODUCTS)
    future_trades = outcomes.select(products=("FUTURE",))
    future_ids = {r["instrument_id"] for r in future_trades} if not outcomes.error else None
    conversion_pairs: set = set()
    try:
        from data.bloomberg import library
        conversion_pairs = {r["key"] for r in _library_in_force(conn, as_of)
                            if r["kind"] == "SPOT" and r.get("role") == library.ROLE_CONVERSION
                            and r["product"] not in _FX_LEG_PRODUCTS + ("FX_OPTION",)}
    except Exception:  # noqa: BLE001 -- then a conversion spot is counted among the FX spots
        conversion_pairs = set()

    out: List[Check] = []
    for mark_type, label, what, trades in (("SPOT", "FX spot", "FX trades", fx_trades),
                                           ("FWD_OUTRIGHT", "FX forward outright", "FX trades", fx_trades),
                                           ("FUTURE_PX", "Futures price", "futures", future_trades)):
        sub = df[df["mark_type"] == mark_type] if not df.empty else df
        if not sub.empty:
            if mark_type != "FUTURE_PX":
                sub = sub[~sub["instrument_id"].map(lambda i: bool(is_lme_instrument(i)))]
            if mark_type == "SPOT" and conversion_pairs:
                # a non-USD future's conversion spot has its own row ("Conversion spots")
                sub = sub[~sub["instrument_id"].isin(conversion_pairs)]
            if mark_type == "FUTURE_PX" and future_ids is not None:
                sub = sub[sub["instrument_id"].isin(future_ids)]
        needed = len(sub)
        if needed == 0:
            out.append(_row(f"{label} coverage", "pass", f"No open positions need a {mark_type} mark today."))
            continue
        missing = sub[sub["status"] == "MISSING"]
        status, sentences = _screen_severity(outcomes, trades, what, marks_short=not missing.empty)
        if missing.empty and status == "pass":
            out.append(_row(f"{label} coverage", "pass",
                             f"All {needed} required {mark_type} marks are present as official for {as_of}."))
            continue
        head = (f"{len(missing)} of {needed} required {mark_type} marks are not on file for {as_of} "
                f"({_names([f'{r.instrument_id} {r.settle_date}' for r in missing.itertuples()], 4)}): the pull did "
                "not get them, or the instrument was not requested."
                if not missing.empty else
                f"All {needed} required {mark_type} marks are on file for {as_of}, but not every {what[:-1]} is "
                "valued on one.")
        out.append(_row(f"{label} coverage", status, " ".join([head] + [s[0].upper() + s[1:] + "." for s in sentences])))
    return out


# --------------------------------------------------------------------------- 4b. the commodity steps (2026-09-28)
# Everything below reads the app's own record of what the book needs from Bloomberg
# (data.bloomberg.library.needed_on with include_unrequestable=True: every row in force on
# the day, LME rows included, the rows the pull cannot ask for flagged), never a second
# definition of "needed"; and the app's own rules for "on file" (inventory.lme_curve_status,
# inventory.contract_dates_inventory, engine.lme.forward_at, live.not_requestable_futures).
# Each check returns one clean pass row when the book has nothing of its kind, and a failure
# to run at all becomes one warning row (`_warn_on_crash`), never an exception.

def _warn_on_crash(label: str, fn, *args) -> List[Check]:
    """One warning row, never an exception, when a check cannot run at all."""
    try:
        return list(fn(*args))
    except Exception as exc:  # noqa: BLE001 -- a diagnostics page must never crash
        return [_row(label, "warning",
                     f"This check could not run ({exc.__class__.__name__}: {exc}); nothing was written.")]


def _names(items, limit: int = 6) -> str:
    """'a, b, c (+2 more)': the first few names, the rest counted."""
    items = list(items)
    shown = ", ".join(str(i) for i in items[:limit])
    return shown + (f" (+{len(items) - limit} more)" if len(items) > limit else "")


def _plural(n: int, word: str, plural: Optional[str] = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def _library_in_force(conn: sqlite3.Connection, as_of: str) -> List[dict]:
    """Every Bloomberg-library row in force on `as_of` (needed_from <= as_of <= needed_until),
    requestable or not, LME included: exactly what the pull and the Data tab's listings work
    from. On a read-only handle the library is worked out in memory when it is behind the
    book (library.rows does that itself), so nothing is written here."""
    from data.bloomberg import library
    return list(library.needed_on(conn, as_of, include_unrequestable=True))


def _instrument_roots(conn: sqlite3.Connection, instrument_ids) -> Dict[str, str]:
    """{instrument_id: base_ccy} for the ids given (a commodity future's or option's base_ccy
    is its contract root id, 'NYMEX:CL')."""
    ids = sorted(set(instrument_ids))
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {k: (v or "") for k, v in conn.execute(
        f"SELECT instrument_id, base_ccy FROM instruments WHERE instrument_id IN ({marks})", ids)}


def _open_commodity_roots(conn: sqlite3.Connection, in_force: List[dict]) -> Dict[str, set]:
    """{root id: {instrument ids}} of the open futures, options on futures (their own and
    their underlying's rows) and LME forwards, from the library rows in force."""
    from data.bloomberg import library
    roots_of = _instrument_roots(conn, {r["key"] for r in in_force if r["kind"] == "FUTURE_PX"})
    out: Dict[str, set] = {}
    for inst, root in roots_of.items():
        if library.is_contract_root(root):
            out.setdefault(root, set()).add(inst)
    for r in in_force:
        if r["kind"] == library.LME_CURVE and library.is_contract_root(r.get("key")):
            out.setdefault(r["key"], set()).add(r["key"])
    return out


def _official_source(conn: sqlite3.Connection, as_of: str, instrument_id: str, settle_date: str,
                     mark_type: str) -> Optional[str]:
    """The source of the official mark for this exact key on `as_of`, or None when there is none."""
    row = conn.execute(
        "SELECT source FROM marks_official WHERE as_of_date = ? AND instrument_id = ? AND settle_date = ? "
        "AND mark_type = ? LIMIT 1", (as_of, instrument_id, settle_date, mark_type)).fetchone()
    return row[0] if row else None


def check_contract_roots_verified(conn: sqlite3.Connection, as_of: str) -> List[Check]:
    """1. How many contract roots of config/contracts.csv a Bloomberg terminal has confirmed
    (bbg_verified), and which roots the open book stands on that are still unverified: their
    tickers, currencies and price scales are best guesses until `py 2_launcher.py bbg-check`
    is run at the terminal (CLAUDE.md "Bloomberg check")."""
    name = "Contract roots verified"
    from data.contracts import load_roots
    roots = load_roots()
    verified = [rid for rid, root in roots.items() if root.bbg_verified]
    head = (f"{len(verified)} of {len(roots)} contract roots in config/contracts.csv are verified on a "
            "Bloomberg terminal")
    open_roots = _open_commodity_roots(conn, _library_in_force(conn, as_of))
    if not open_roots:
        return [_row(name, "pass", head + "; no open futures, options on futures or LME forwards in the book, "
                                          "so none of the unverified roots is in use.")]
    unverified = sorted(rid for rid in open_roots if rid in roots and not roots[rid].bbg_verified)
    unknown = sorted(rid for rid in open_roots if rid not in roots)
    if not unverified and not unknown:
        return [_row(name, "pass", head + f"; every root in the open book ({_names(sorted(open_roots))}) is verified.")]
    message = head + (f"; {len(unverified)} of the {len(open_roots)} roots in the open book are unverified "
                      f"({_names(unverified)}): their Bloomberg tickers and price scales are best guesses until "
                      "`py 2_launcher.py bbg-check` is run at the terminal and its worksheet applied."
                      if unverified else "")
    if unknown:
        message += (f" {_plural(len(unknown), 'root')} of the open book {'is' if len(unknown) == 1 else 'are'} not in "
                    f"config/contracts.csv at all ({_names(unknown)}).")
    return [_row(name, "warning", message.strip())]


def check_not_requestable(conn: sqlite3.Connection, as_of: str,
                          outcomes: Optional[_Outcomes] = None) -> List[Check]:
    """2. Open futures, options on futures and LME metals whose root has no Bloomberg
    ticker (the library marks them not requestable): a pull never asks for them, so no new
    price can arrive. The futures list is live's own (`not_requestable_futures`, the status
    file's "not_requestable" block). FAIL only when such a trade shows no value on screen;
    WARNING while it is still valued off the marks on file (estimated or filled)."""
    name = "Futures with no Bloomberg ticker"
    from data.bloomberg import library
    from data.bloomberg.live import not_requestable_futures
    in_force = _library_in_force(conn, as_of)
    open_rows = [r for r in in_force if r["kind"] in ("FUTURE_PX", library.LME_CURVE)]
    if not open_rows:
        return [_row(name, "pass", "No open futures, options on futures or LME forwards; nothing to request.")]
    entries = list(not_requestable_futures(conn, as_of))
    lme_blocked = {r["key"]: r.get("reason", "") for r in in_force
                   if r["kind"] == library.LME_CURVE and not r.get("requestable", True)}
    contracts = sorted({r["key"] for r in open_rows})
    if not entries and not lme_blocked:
        return [_row(name, "pass", f"All {_plural(len(contracts), 'open contract')} carry a Bloomberg ticker the pull "
                                   "can ask for.")]
    said = [f"{e['instrument_id']} ({e['reason']})" for e in entries]
    said += [f"{root} LME curve ({reason})" for root, reason in sorted(lme_blocked.items())]
    affected_trades = {t for e in entries for t in e.get("trade_ids", [])}
    affected_trades |= {r["trade_id"] for r in in_force if r["kind"] == library.LME_CURVE and r["key"] in lme_blocked}
    if outcomes is None:
        outcomes = _Outcomes(conn, as_of)
    status, sentences = _screen_severity(outcomes, outcomes.select(trade_ids=affected_trades), "affected trades",
                                         marks_short=True)
    if status == "pass" and not outcomes.error:
        status = "warning"          # valued today off marks on file, but nothing new can ever arrive
        sentences = ["their trades are still valued off the marks on file today"] + sentences
    head = (f"{len(said)} of {_plural(len(contracts), 'open contract')} have no Bloomberg ticker and are never "
            f"asked for, so no new price can arrive: {_names(said)}.")
    tail = ("The fix is the root's Bloomberg root in config/contracts.csv: `py 2_launcher.py bbg-check` at the "
            "terminal writes the worksheet and `contracts-apply` applies it.")
    return [_row(name, status, " ".join([head] + [s[0].upper() + s[1:] + "." for s in sentences] + [tail]))]


def check_contract_dates(conn: sqlite3.Connection, as_of: str) -> List[Check]:
    """3. Whether Bloomberg's own last-trade / first-notice (or option expiry) dates are
    stored in contract_static for every open future and option on a future; until then the
    contract runs on contract-master's conservative estimate. The inventory is
    data.bloomberg.inventory.contract_dates_inventory, the Data tab's own panel."""
    name = "Contract dates stored"
    from data.bloomberg.inventory import STATUS_ON_FILE, contract_dates_inventory
    df = contract_dates_inventory(conn, as_of)
    if df.empty:
        return [_row(name, "pass", "No open futures or options on futures; no contract dates to store.")]
    total = len(df)
    missing = df[df["status"] != STATUS_ON_FILE]
    if missing.empty:
        return [_row(name, "pass", f"Bloomberg's own dates are stored for all {_plural(total, 'open contract')} "
                                   "(contract_static); none runs on an estimated expiry.")]
    askable = [r.contract_id for r in missing.itertuples() if not r.reason]
    blocked = [f"{r.contract_id} ({r.reason})" for r in missing.itertuples() if r.reason]
    message = (f"{len(missing)} of {_plural(total, 'open contract')} still run on estimated expiry dates, with no "
               f"Bloomberg dates in contract_static: {_names([r.contract_id for r in missing.itertuples()])}.")
    if askable:
        message += (f" The next Pull Bloomberg now asks for {len(askable)} of them and moves the contracts to "
                    "Bloomberg's dates.")
    if blocked:
        message += f" {len(blocked)} cannot be asked until the root has a ticker ({_names(blocked, 4)})."
    return [_row(name, "warning", message)]


def check_options_on_futures(conn: sqlite3.Connection, as_of: str,
                             outcomes: Optional[_Outcomes] = None) -> List[Check]:
    """4. Each open option on a commodity future (CMDTY_OPTION) needs, on `as_of`: Bloomberg's
    own price of the option as an official FUTURE_PX on its own instrument (all its P&L
    needs: the row's severity is what the screens show for the option, a dash = fail,
    estimated or filled = warning); and for its Greeks only (a miss is a warning at worst)
    its underlying future's official FUTURE_PX, an OIS curve for its discount currency (its
    own currency when the pricer has one, else USD SOFR: library.option_discount_ccy) and
    official DELTA / GAMMA / THETA / VEGA marks under QL_OPTIONS_PRICER."""
    from data.bloomberg import library
    from data.bloomberg.inventory import _holds_ois_curve
    in_force = [r for r in _library_in_force(conn, as_of) if r["product"] == library.CMDTY_OPTION]
    own = {r["key"]: r for r in in_force if r["kind"] == "FUTURE_PX" and r.get("role") == library.ROLE_PAIR}
    if not own:
        return [_row("Options on futures", "pass", "No open options on futures; nothing to check.")]
    options = sorted(own)
    n = len(options)
    out: List[Check] = []
    by_trade = {r["trade_id"]: r["key"] for r in in_force if r["kind"] == "FUTURE_PX" and r.get("role") == library.ROLE_PAIR}
    if outcomes is None:
        outcomes = _Outcomes(conn, as_of)

    # 4a. the option's own price (its P&L): severity from the screens
    no_ticker = [o for o in options if not own[o].get("requestable", True)]
    missing = [o for o in options if o not in no_ticker
               and _official_source(conn, as_of, o, own[o]["settle_date"], "FUTURE_PX") is None]
    trades = outcomes.select(products=(library.CMDTY_OPTION,))
    status, sentences = _screen_severity(outcomes, trades, "options on futures", marks_short=bool(missing or no_ticker))
    if not missing and not no_ticker and status == "pass":
        out.append(_row("Options on futures: price", "pass",
                        f"All {_plural(n, 'open option')} on futures have Bloomberg's own price as an official mark for {as_of}."))
    else:
        head = ""
        if missing:
            head = (f"{len(missing)} of {_plural(n, 'open option')} on futures have no official price (FUTURE_PX on "
                    f"the option's own instrument) for {as_of}: {_names(missing)}.")
        if no_ticker:
            head += (f" {len(no_ticker)} have no Bloomberg ticker and are never asked for "
                     f"({_names(no_ticker)}); see 'Futures with no Bloomberg ticker'.")
        if not head:
            head = f"Every open option on futures has an official price for {as_of}, but not every one is valued on it."
        out.append(_row("Options on futures: price", status,
                        " ".join([head.strip()] + [s[0].upper() + s[1:] + "." for s in sentences])))

    # 4b. the underlying future's price (Greeks)
    underlying_of: Dict[str, dict] = {}
    for r in in_force:
        if r["kind"] == "FUTURE_PX" and r.get("role") == library.ROLE_UNDERLYING and r["trade_id"] in by_trade:
            underlying_of.setdefault(by_trade[r["trade_id"]], r)
    no_underlying = [o for o in options if o not in underlying_of]
    und_missing = [f"{o} (on {underlying_of[o]['key']})" for o in options if o in underlying_of
                   and _official_source(conn, as_of, underlying_of[o]["key"], underlying_of[o]["settle_date"], "FUTURE_PX") is None]
    if no_underlying or und_missing:
        message = ""
        if und_missing:
            message = (f"{len(und_missing)} of {n} underlying futures have no official price for {as_of}: "
                       f"{_names(und_missing)}; the option's Greeks cannot be priced (its P&L is unaffected).")
        if no_underlying:
            message += (f" {len(no_underlying)} option(s) have no underlying future on file at all "
                        f"({_names(no_underlying)}); the ingest writes it with the option, so re-upload the blotter.")
        out.append(_row("Options on futures: underlying price", "warning", message.strip()))
    else:
        out.append(_row("Options on futures: underlying price", "pass",
                        f"Every underlying future of the {n} open options has an official price for {as_of}."))

    # 4c. the discount curve (Greeks)
    quote_ccy = {k: v for k, v in conn.execute(
        f"SELECT instrument_id, quote_ccy FROM instruments WHERE instrument_id IN ({','.join('?' * n)})", options)}
    curve_ccy: Dict[str, str] = {}
    for r in in_force:
        if r["kind"] == "OIS_CURVE" and r["trade_id"] in by_trade:
            curve_ccy.setdefault(by_trade[r["trade_id"]], r["key"])
    fallbacks = sorted({f"{quote_ccy.get(o, '?')} -> {curve_ccy[o]} SOFR" for o in options
                        if o in curve_ccy and curve_ccy[o] != quote_ccy.get(o)})
    needed_ccys = sorted({curve_ccy[o] for o in options if o in curve_ccy})
    absent = [c for c in needed_ccys if not _holds_ois_curve(conn, as_of, c)]
    note = f" {_plural(len(fallbacks), 'currency', 'currencies')} with no OIS set-up discount on USD SOFR ({_names(fallbacks)})." if fallbacks else ""
    if absent:
        hit = [o for o in options if curve_ccy.get(o) in absent]
        out.append(_row("Options on futures: discount curve", "warning",
                        f"No Bloomberg OIS quotes for {as_of} in curve_quotes for {_names(absent)}, which "
                        f"{_plural(len(hit), 'open option')} on futures discount on ({_names(hit)}); their Greeks cannot "
                        "be priced until the pull's curves step writes them (the P&L needs no curve)." + note))
    else:
        out.append(_row("Options on futures: discount curve", "pass",
                        f"OIS quotes for {as_of} are on file for every discount currency the {n} open options need "
                        f"({_names(needed_ccys) or 'none needed'})." + note))

    # 4d. the Greeks themselves
    greek_missing: Dict[str, List[str]] = {}
    for greek in ("DELTA", "GAMMA", "THETA", "VEGA"):
        have = {r[0] for r in conn.execute(
            f"SELECT DISTINCT instrument_id FROM marks_official WHERE mark_type = ? AND as_of_date = ? "
            f"AND instrument_id IN ({','.join('?' * n)})", (greek, as_of, *options))}
        lacking = [o for o in options if o not in have]
        if lacking:
            greek_missing[greek] = lacking
    if greek_missing:
        sets = {tuple(lst) for lst in greek_missing.values()}
        if len(sets) == 1:              # the same options lack every Greek listed: name them once
            lst = next(iter(sets))
            said = f"{', '.join(greek_missing)}: {len(lst)} of {n} ({_names(lst, 4)})"
        else:
            said = "; ".join(f"{g}: {len(lst)} of {n} ({_names(lst, 4)})" for g, lst in greek_missing.items())
        out.append(_row("Options on futures: Greeks", "warning",
                        f"Official Greeks (QL_OPTIONS_PRICER) are missing for {as_of} on some open options on futures: "
                        f"{said}. The pull's options step prices them from the option's price, the underlying's price "
                        "and the discount curve above; the P&L itself needs none of them."))
    else:
        out.append(_row("Options on futures: Greeks", "pass",
                        f"All {n} open options on futures carry official DELTA, GAMMA, THETA and VEGA for {as_of}."))
    return out


def check_lme_curves(conn: sqlite3.Connection, as_of: str,
                     outcomes: Optional[_Outcomes] = None) -> List[Check]:
    """5. For each LME metal with an open forward on `as_of`: is its curve complete that day
    by the engine's own rule (inventory.lme_curve_status: cash SPOT and 3M FWD_OUTRIGHT
    official, each at the close on a past day), and is each ticket's prompt outright on file
    (BBG_BFXFORWARD at a pillar Bloomberg quoted, or BBG_INTERP between two pillars, both
    official for FWD_OUTRIGHT); a prompt with neither is checked against engine.lme.forward_at,
    which says whether the valuation can still read it between the pillars. Both rows take
    their severity from what the screens show for the tickets: a dash = fail, a ticket
    estimated or filled = warning, every ticket on an official mark = pass."""
    from data.bloomberg import library
    from data.bloomberg.inventory import lme_curve_status
    from engine.lme import forward_at
    in_force = _library_in_force(conn, as_of)
    curve_rows = {r["key"]: r for r in in_force if r["kind"] == library.LME_CURVE}
    if not curve_rows:
        return [_row("LME curves", "pass", "No open LME forwards; no LME curve is needed today.")]
    today = _today_ny()
    roots = sorted(curve_rows)
    out: List[Check] = []
    if outcomes is None:
        outcomes = _Outcomes(conn, as_of)
    tickets_of = {}
    for r in in_force:
        if r["product"] in library.LME_PRODUCTS and r["kind"] == "FWD_OUTRIGHT":
            tickets_of.setdefault(r["key"], set()).add(r["trade_id"])

    incomplete = []
    for root in roots:
        status = lme_curve_status(conn, as_of, root, today=today)
        if not status.get("complete"):
            why = " and ".join(status.get("missing") or ["curve"])
            reason = curve_rows[root].get("reason") or ""
            incomplete.append(f"{root}: {why} not official" + (f" ({reason})" if reason else ""))
    if incomplete:
        affected = {t for root in roots for t in tickets_of.get(root, set())
                    if any(i.startswith(root + ":") for i in incomplete)}
        status, sentences = _screen_severity(outcomes, outcomes.select(trade_ids=affected), "LME forwards", marks_short=True)
        head = (f"{len(incomplete)} of {_plural(len(roots), 'LME curve')} the open forwards need "
                f"{'is' if len(incomplete) == 1 else 'are'} not complete for {as_of} (the engine's rule: the cash "
                f"price and the 3-month outright both official that day): {_names(incomplete)}. The pull's LME "
                "step asks each pillar's PX_LAST.")
        out.append(_row("LME curves", status, " ".join([head] + [s[0].upper() + s[1:] + "." for s in sentences])))
    else:
        out.append(_row("LME curves", "pass",
                        f"All {_plural(len(roots), 'LME curve')} ({_names(roots)}) are complete for {as_of}: cash and "
                        "3M official."))

    prompts = sorted({(r["key"], r["settle_date"]) for r in in_force
                      if r["product"] in library.LME_PRODUCTS and r["kind"] == "FWD_OUTRIGHT"})
    if not prompts:
        out.append(_row("LME prompt outrights", "pass", "No open LME prompt to mark."))
        return out
    direct, interp, readable, absent = [], [], [], []
    for root, prompt in prompts:
        source = _official_source(conn, as_of, root, prompt, "FWD_OUTRIGHT")
        label = f"{root} {prompt}"
        if source == "BBG_INTERP":
            interp.append(label)
        elif source:
            direct.append(label)
        else:
            try:
                got = forward_at(conn, root, prompt, as_of)
            except ValueError as exc:            # a stored value that is not a number: a data error
                absent.append(f"{label} ({exc})")
                continue
            (readable if got is not None else absent).append(label)
    total = len(prompts)
    all_tickets = outcomes.select(products=library.LME_PRODUCTS)
    status, sentences = _screen_severity(outcomes, all_tickets, "LME forwards", marks_short=bool(absent or readable))
    if not absent and not readable and status == "pass":
        detail = f"{len(direct)} at a pillar Bloomberg quoted" + (f", {len(interp)} interpolated between pillars (BBG_INTERP, official for a broken prompt)" if interp else "")
        out.append(_row("LME prompt outrights", "pass",
                        f"All {_plural(total, 'open LME prompt')} have an official outright for {as_of}: {detail}."))
        return out
    parts = []
    if absent:
        parts.append(f"{len(absent)} of {_plural(total, 'open LME prompt')} have no outright on the {as_of} curve and "
                     f"cannot be read between its pillars (beyond the last pillar, or no curve on file): {_names(absent)}.")
    if readable:
        parts.append(f"{len(readable)} of {_plural(total, 'open LME prompt')} have no stored outright for {as_of} but "
                     f"sit between pillars on file, so the valuation reads them off the curve: {_names(readable)}; the "
                     "pull's LME step writes them as BBG_INTERP.")
    if not parts:
        parts.append(f"Every open LME prompt has an outright on file for {as_of}, but not every ticket is valued on it.")
    out.append(_row("LME prompt outrights", status, " ".join(parts + [s[0].upper() + s[1:] + "." for s in sentences])))
    return out


def check_conversion_spots(conn: sqlite3.Connection, as_of: str,
                           outcomes: Optional[_Outcomes] = None) -> List[Check]:
    """6. Every open non-USD future or option on a future converts its P&L at the official
    SPOT of its currency's USD pair on the valuation date (role CONVERSION in the library).
    A missing pair is named; the severity is what the screens show for the contracts that
    need it (a dash = fail, filled from an earlier close = warning)."""
    name = "Conversion spots"
    from data.bloomberg import library
    in_force = _library_in_force(conn, as_of)
    fx_products = _FX_LEG_PRODUCTS + ("FX_OPTION",)
    conv = [r for r in in_force if r["kind"] == "SPOT" and r.get("role") == library.ROLE_CONVERSION
            and r["product"] not in fx_products and r["product"] not in library.LME_PRODUCTS]
    if not conv:
        return [_row(name, "pass", "No open non-USD futures or options on futures; no conversion spot is needed.")]
    contract_of = {r["trade_id"]: r["key"] for r in in_force
                   if r["kind"] == "FUTURE_PX" and r.get("role") == library.ROLE_PAIR}
    users: Dict[str, set] = {}
    trades_of: Dict[str, set] = {}
    for r in conv:
        users.setdefault(r["key"], set()).add(contract_of.get(r["trade_id"], r["trade_id"]))
        trades_of.setdefault(r["key"], set()).add(r["trade_id"])
    pairs = sorted(users)
    missing = [p for p in pairs if _official_source(conn, as_of, p, as_of, "SPOT") is None]
    if not missing:
        return [_row(name, "pass",
                     f"All {_plural(len(pairs), 'USD-conversion spot')} ({_names(pairs)}) are official for {as_of}, covering "
                     f"{_plural(len({c for s in users.values() for c in s}), 'non-USD contract')}.")]
    if outcomes is None:
        outcomes = _Outcomes(conn, as_of)
    affected = {t for p in missing for t in trades_of[p]}
    status, sentences = _screen_severity(outcomes, outcomes.select(trade_ids=affected), "non-USD contracts", marks_short=True)
    said = [f"{p} (needed by {_names(sorted(users[p]), 3)})" for p in missing]
    head = (f"{len(missing)} of {_plural(len(pairs), 'USD-conversion spot')} have no official SPOT for {as_of}: "
            f"{_names(said)}.")
    return [_row(name, status, " ".join([head] + [s[0].upper() + s[1:] + "." for s in sentences]))]


# --------------------------------------------------------------------------- 5. OIS curves for the FX options
_OIS_INDEX = {"USD": "SOFR", "EUR": "ESTR", "GBP": "SONIA", "JPY": "TONA",
              "CHF": "SARON", "CAD": "CORRA", "AUD": "AONIA"}


def check_ois_curve_coverage(conn: sqlite3.Connection, as_of: str,
                             outcomes: Optional[_Outcomes] = None) -> List[Check]:
    """The OIS discount curves the open FX options price off (engine/options/rates.py):
    each currency of an open option's pair with an OIS index in scope needs that day's
    quotes in curve_quotes. A currency with no OIS index takes its rate another way
    (engine/options/rates.py) and is named, not failed. A day without the quotes fails only
    when an FX option shows a dash on screen; while the options are valued off the PREMIUM
    marks on file (estimated or filled) it is a warning (2026-09-28)."""
    out: List[Check] = []
    if outcomes is None:
        outcomes = _Outcomes(conn, as_of)
    fx_options = outcomes.select(products=("FX_OPTION",))
    screen_status, screen_sentences = _screen_severity(outcomes, fx_options, "FX options", marks_short=True)
    if screen_status == "pass":
        screen_status = "warning"      # the marks are on file today, but the curve is not: nothing new can be priced
    screen_tail = " ".join(s[0].upper() + s[1:] + "." for s in screen_sentences)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    except sqlite3.Error as exc:
        return [_row("OIS curve coverage (FX options)", "fail", f"Could not read the schema ({exc}).")]
    if "trades" not in tables:
        return [_row("OIS curve coverage (FX options)", "warning", "No trades table found; skipping.")]
    try:
        pairs = conn.execute(
            "SELECT DISTINCT i.base_ccy, i.quote_ccy FROM trades t JOIN instruments i USING (instrument_id) "
            "WHERE t.product = 'FX_OPTION' AND i.expiry_date >= ?", (as_of,)).fetchall()
    except sqlite3.Error as exc:
        return [_row("OIS curve coverage (FX options)", "fail", f"Could not read FX option trades ({exc}).")]
    ccys = {c for pair in pairs for c in pair if c}
    if not ccys:
        return [_row("OIS curve coverage (FX options)", "pass", "No open FX options; no OIS curve is required today.")]

    no_index = sorted(ccys - _OIS_INDEX.keys())
    if no_index:
        out.append(_row("OIS curve coverage (FX options)", "pass",
                         f"{', '.join(no_index)} has no OIS index in scope (USD/EUR/GBP/JPY/CHF/CAD/AUD only); "
                         "the options pricer takes its rate another way (engine/options/rates.py)."))
    in_scope = sorted(ccys & _OIS_INDEX.keys())
    if in_scope and "curve_quotes" not in tables:
        out.append(_row("OIS curve coverage (FX options)", "fail",
                         "curve_quotes table does not exist, but open FX options need an OIS curve in "
                         f"{', '.join(in_scope)}."))
        return out
    for ccy in in_scope:
        idx = _OIS_INDEX[ccy]
        n = conn.execute("SELECT COUNT(*) FROM curve_quotes WHERE as_of_date = ? AND ccy = ? AND \"index\" = ?",
                          (as_of, ccy, idx)).fetchone()[0]
        if n == 0:
            out.append(_row(f"{ccy}-{idx} curve quotes for {as_of}", screen_status,
                             f"No {idx} curve quotes found for {as_of} in curve_quotes, but an open FX option on a "
                             f"{ccy} pair needs one -- the Bloomberg curve pull may not have run. " + screen_tail))
        else:
            out.append(_row(f"{ccy}-{idx} curve quotes for {as_of}", "pass",
                             f"{n} {idx} curve quote(s) present for {as_of}."))
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


def check_option_coverage(conn: sqlite3.Connection, as_of: str, db_path: Optional[Path] = None,
                          outcomes: Optional[_Outcomes] = None) -> List[Check]:
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
    # 2026-09-28: the severity is what the screens show for the options that need the mark
    # (a dash = fail; estimated or filled = warning). DELTA is not P&L: warning at worst.
    if outcomes is None:
        outcomes = _Outcomes(conn, as_of)
    for mark_type in ("PREMIUM", "DELTA"):
        if not priceable:
            continue
        have = {r[0] for r in conn.execute(
            "SELECT DISTINCT instrument_id FROM marks_official WHERE mark_type = ? AND as_of_date = ?",
            (mark_type, as_of))}
        missing = [inst for inst in priceable if inst not in have]
        if missing:
            affected = outcomes.select(products=("FX_OPTION",), instrument_ids=set(missing))
            status, sentences = _screen_severity(outcomes, affected, "FX options", marks_short=True)
            if mark_type == "DELTA" and status == "fail":
                status = "warning"
            if status == "pass":
                status = "warning"
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
            if sentences:
                message += " " + " ".join(s[0].upper() + s[1:] + "." for s in sentences)
            out.append(_row(f"FX option {mark_type} coverage", status, message))
        else:
            out.append(_row(f"FX option {mark_type} coverage", "pass",
                             f"All {len(priceable)} priceable open option(s) have an official {mark_type} for {as_of}."))
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
def check_clock(as_of: str, now: Optional[datetime] = None) -> List[Check]:
    """The feed stamps marks with the book date: the New York date until 17:00 New York,
    the next date from then on (`data.bloomberg.live.book_today`, user decision 2026-09-22).
    A PC clock or zone that is off, or an as-of date that is not the book date, is the
    usual reason 'everything is missing'. `now` is for tests (a tz-aware datetime)."""
    try:
        from zoneinfo import ZoneInfo
        now_ny = (now if now is not None else datetime.now(ZoneInfo("America/New_York"))
                  ).astimezone(ZoneInfo("America/New_York"))
    except Exception as exc:
        return [_row("PC clock / New York date", "fail",
                     f"Cannot resolve America/New_York ({exc.__class__.__name__}); tzdata may be missing -- "
                     "run  py -3 2_launcher.py setup.")]
    book_date = _today_ny(now_ny)
    local = now_ny.astimezone()
    detail = (f"Local time {local.strftime('%Y-%m-%d %H:%M %Z')} = New York {now_ny.strftime('%Y-%m-%d %H:%M')}; "
              f"marks pulled now are stamped {book_date}, the book date (New York, rolled at 17:00).")
    if as_of != book_date:
        return [_row("PC clock / New York date", "warning",
                     detail + f" The checks above ran for as-of {as_of}, which is not the book date "
                              f"(New York, rolled at 17:00), {book_date}; a Ladder or Market data date picker "
                              "left on an old date finds no official marks.")]
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


# --------------------------------------------------------------------------- 7c. the last pull's commodity steps
def check_last_pull_commodity_blocks(db_path: Optional[Path]) -> List[Check]:
    """7. What the last connected pull recorded for the commodity steps, read from the status
    file and summarised with live's own sentences: status["contract_dates"]
    (live.contract_dates_summary), status["lme"] (live.lme_summary), status["not_requestable"]
    and the options-on-futures count of status["options"]. One warning "not yet exercised"
    when no pull has connected on this database, so the steps have never run."""
    name = "Last pull: commodity steps"
    if db_path is None:
        return [_row(name, "warning", "not yet exercised: no database path resolved, so no pull status can be read.")]
    from data.bloomberg.live import contract_dates_summary, futures_options_sentence, lme_summary, read_status
    status = read_status(db_path)
    if not status:
        return [_row(name, "warning", "not yet exercised: no Bloomberg pull has run on this database.")]
    when = status.get("time", "an unknown time")
    if not status.get("connected"):
        return [_row(name, "warning",
                     f"not yet exercised: the last pull ({when}) did not connect "
                     f"({status.get('reason', 'unknown reason')}), so the contract dates, LME curves and "
                     "options-on-futures steps have not run yet.")]
    if not any(k in status for k in ("contract_dates", "lme", "not_requestable")):
        return [_row(name, "warning",
                     f"not yet exercised: the last pull ({when}) predates the commodity steps (no contract_dates, lme or "
                     "not_requestable block recorded); press Pull Bloomberg now to run them.")]
    out: List[Check] = []

    block = status.get("contract_dates") if isinstance(status.get("contract_dates"), dict) else {}
    sentence = contract_dates_summary(block) or "no contract dates were asked for (all stored already, or no futures in the book)"
    applied = block.get("applied") if isinstance(block.get("applied"), dict) else {}
    if block.get("error") or applied.get("error"):
        status_cd = "fail"
    elif block.get("failed"):
        status_cd = "warning"
    else:
        status_cd = "pass"
    failed = block.get("failed") or []
    detail = ""
    if failed:
        said = [f"{f.get('ticker') or f.get('instrument_id') or '?'}: {f.get('reason') or f.get('detail') or 'no date'}"
                if isinstance(f, dict) else str(f) for f in failed]
        detail = f" Gave no date: {_names(said, 4)}."
    if block.get("error"):
        detail += f" The step stopped: {block['error']}."
    out.append(_row("Last pull: contract dates", status_cd, f"Last pull ({when}): {sentence}.{detail}"))

    if "lme" not in status:
        out.append(_row("Last pull: LME curves", "pass",
                        f"Last pull ({when}) had no LME step to run (no open LME forward in the book)."))
    else:
        block = status.get("lme") if isinstance(status.get("lme"), dict) else {}
        sentence = lme_summary(block) or "no LME forward in the book, nothing asked"
        if block.get("error"):
            status_lme = "fail"
        elif block.get("missing"):
            status_lme = "warning"
        else:
            status_lme = "pass"
        detail = ""
        if block.get("missing"):
            said = [f"{m.get('root_id', '?')} {m.get('ticker') or m.get('settle_date', '')}: {m.get('reason', '')}".strip()
                    for m in block["missing"] if isinstance(m, dict)]
            detail = f" Without a mark: {_names(said, 4)}."
        out.append(_row("Last pull: LME curves", status_lme, f"Last pull ({when}): {sentence}.{detail}"))

    not_requestable = status.get("not_requestable") or []
    if not_requestable:
        said = [f"{e.get('instrument_id', '?')} ({e.get('reason', 'no ticker')})" for e in not_requestable if isinstance(e, dict)]
        out.append(_row("Last pull: not requested", "warning",
                        f"Last pull ({when}) left {_plural(len(not_requestable), 'contract')} unrequested for want of a "
                        f"Bloomberg ticker: {_names(said)}."))
    else:
        out.append(_row("Last pull: not requested", "pass",
                        f"Last pull ({when}) had a Bloomberg ticker for every contract the book needed."))

    options = status.get("options")
    if isinstance(options, dict):
        priced = int(options.get("futures_options_priced") or 0)
        sentence = options.get("futures_options_summary") or futures_options_sentence(priced) or "no option on futures priced"
        skipped = options.get("skipped") if isinstance(options.get("skipped"), list) else []
        if options.get("error"):
            status_opt, detail = "fail", f" The options step stopped: {options['error']}."
        elif skipped:
            said = [f"{s.get('trade_id', '?')}: {s.get('reason', '')}" for s in skipped if isinstance(s, dict)]
            status_opt, detail = "warning", f" {_plural(len(skipped), 'option')} skipped ({_names(said, 4)}; FX options included)."
        else:
            status_opt, detail = "pass", ""
        out.append(_row("Last pull: options on futures", status_opt, f"Last pull ({when}): {sentence}.{detail}"))
    return out


# --------------------------------------------------------------------------- orchestration
def run_bloomberg_diagnostics(db_path: Optional[str] = None, as_of: Optional[str] = None,
                              host: Optional[str] = None, port: Optional[int] = None) -> List[Check]:
    """Runs every check and returns a flat list of {name, status, message} dicts, newest
    to CLAUDE.md's own ordering (session, official-source mapping, FX/futures coverage,
    OIS curve coverage for the FX options, snapped_at, last pull). Never raises: any single check that blows
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
        # The book as the screens show it, valued once (its constructor never raises).
        outcomes = _Outcomes(conn, resolved_as_of)
        if outcomes.error:
            results.append(_row("Book valuation", "warning",
                                f"The book could not be valued for {resolved_as_of} through the screens' own reader "
                                f"({outcomes.error}); the coverage rows below judge the marks alone."))
        _safe("FX/futures marks coverage", check_fx_and_future_coverage, conn, resolved_as_of, outcomes)
        # The commodity steps (2026-09-28): each one warning row, never an exception, if it cannot run.
        for label, fn, with_outcomes in (("Contract roots verified", check_contract_roots_verified, False),
                                         ("Futures with no Bloomberg ticker", check_not_requestable, True),
                                         ("Contract dates stored", check_contract_dates, False),
                                         ("Options on futures", check_options_on_futures, True),
                                         ("LME curves", check_lme_curves, True),
                                         ("Conversion spots", check_conversion_spots, True)):
            args = (conn, resolved_as_of, outcomes) if with_outcomes else (conn, resolved_as_of)
            results.extend(_warn_on_crash(label, fn, *args))
        _safe("OIS curve coverage (FX options)", check_ois_curve_coverage, conn, resolved_as_of, outcomes)
        _safe("FX option marks coverage", check_option_coverage, conn, resolved_as_of, resolved_db, outcomes)
        _safe("snapped_at carries a resolved offset", check_snapped_at_offset, conn, resolved_as_of)
        conn.close()
        _safe("PC clock / New York date", check_clock, resolved_as_of)

    _safe("FX vol ticker assumptions", check_unverified_assumptions, resolved_db)

    _safe("Last marks pull", check_last_pull, resolved_db, resolved_as_of)
    _safe("Past closes (backfill)", check_backfill_report, resolved_db)
    results.extend(_warn_on_crash("Last pull: commodity steps", check_last_pull_commodity_blocks, resolved_db))

    return results


# --------------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=None, help="SQLite database (default: data.paths.get_db_path() or $RISK_DB)")
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
