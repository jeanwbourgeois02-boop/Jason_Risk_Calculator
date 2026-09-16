#!/usr/bin/env python
"""Standalone Bloomberg-machine script: pull marks and write the canonical marks CSV.

Runs on a machine with `blpapi` and network access to a running Bloomberg Terminal /
B-PIPE (`//blp/refdata` service on localhost:8194 by default). Deliberately dependency-
light (blpapi, stdlib only) and self-contained: it does NOT import anything from this
repository (data.bloomberg.marks_csv etc.), so it can be copied as a single file to the
Bloomberg machine, which has no access to this repo.

CLI (normal pull):
    py -3 pull_marks.py --request req.csv --as-of 2026-08-17 --out marks.csv
    py -3 pull_marks.py --request req.csv --as-of 2026-08-17 --out marks.csv \
        --host localhost --port 8194
    py -3 pull_marks.py --request req.csv --as-of 2026-08-17 --out marks.csv \
        --allow-stale-as-of   # only needed when --as-of != today in America/New_York

CLI (probe mode -- no request CSV needed, explores which fields/overrides work on this
terminal without touching any marks CSV):
    py -3 pull_marks.py --probe --as-of 2026-08-17 --out probe

Every run (pull or probe) ALWAYS writes a diagnostics file at "<out>.diag.json"
(try/finally: written even on an unhandled exception, with the traceback included).
See the "Diagnostics" section below for its structure. A pull run's marks CSV is
"partial" whenever the set of (instrument_id, settle_date, mark_type) keys actually
written differs from the set requested -- for ANY reason: a stage TIMEOUT, a
SECURITY_ERROR/FIELD_EXCEPTION/NO_VALUE on one ticker, a rejected tenor-interpolation, or
anything else that causes one row to be skipped while the rest of the run looks fine. The
run never exits 0 unless every requested key was written; `summary.marks_csv_partial` and
`summary.failures` (one entry per missing key, with a classification and detail) make
this unambiguous downstream. Exit code table: 0 OK (all requested keys written), 2 bad
`--as-of` (either not a valid ISO date, or stale/refused -- see check_not_stale), 3
unhandled exception, 4 session/service open failure, 5 marks CSV partial (one or more
requested keys missing, for any reason), 6 tz prerequisite missing (America/New_York
zoneinfo not resolvable, e.g. no `tzdata` package on stock Windows Python -- see _ny()).

Request CSV columns: instrument_id, bbg_ticker, settle_date, mark_type
  (mark_type in {SPOT, FWD_OUTRIGHT, FUTURE_PX}; produced by
  data.bloomberg.marks_csv.export_request on the app side).

Output CSV columns (mirrors data.bloomberg.marks_csv.MARKS_COLUMNS -- duplicated here,
not imported, per the single-file constraint):
    as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at

Staleness guard: SPOT and FUTURE_PX are pulled via HistoricalDataRequest for the exact
--as-of date, so they are correct however long after --as-of the script is run.
FWD_OUTRIGHT (both the direct broken-date path and the standard-tenor fallback) has no
historical equivalent here and is necessarily a live ReferenceDataRequest snapshot: if
--as-of is not today in America/New_York, that live snapshot would be mislabeled as a
prior date's close. The script refuses to run any FWD_OUTRIGHT request when
--as-of != today unless --allow-stale-as-of is passed, in which case it proceeds and
logs a clear warning to stderr.

Field / override choices (see fetch_reference / fetch_historical / fetch_fwd_outright
docstrings below for exact names):
  - SPOT: HistoricalDataRequest, field PX_LAST, single date = as_of (consistent with
    FUTURE_PX: both read the closing snapshot for that date rather than a live quote).
  - FUTURE_PX: HistoricalDataRequest, field PX_SETTLE, single date = as_of. PX_SETTLE
    (not PX_LAST) because CLAUDE.md's FUTURES row description says "Price = settlement
    price", and marks.mark_type FUTURE_PX should match that, not an intraday last trade.
  - FWD_OUTRIGHT direct (broken/non-standard settle date): ReferenceDataRequest on
    field FWD_CURVE_QUOTE_FORMAT (override) = 'OUTRIGHTS' with override SETTLE_DT =
    the target settle_date, field FWD_CURVE, ticker '<PAIR> Curncy'. UNVERIFIED -- this
    is the field/override combination believed correct for a single broken-date
    outright via ReferenceDataRequest, but has not been checked against a live
    terminal. See fetch_fwd_outright_direct(). On a real terminal FWD_CURVE is a bulk
    field (a list of rows, not a scalar) when the override does not pin it down to a
    single value; any non-scalar response is treated as "no direct value" (falls back
    to tenor interpolation, logged as a warning), never passed to float(). --probe mode
    also tries two alternative candidates (REFERENCE_DATE override; an
    FX_FWD_OUTRIGHT-style field) -- see run_probe() -- to help decide between them on a
    real terminal (docs/open-questions.md item 27).
  - FWD_OUTRIGHT fallback (standard-tenor interpolation): forward POINTS for standard
    tenors '<PAIR><TENOR> Curncy' (SP, 1W, 2W, 1M, 2M, 3M, 6M, 1Y -- ON and TN are
    deliberately excluded: their settle dates fall before spot and their points use the
    pre-spot convention, outright = spot - points for TN, which this module's
    spot + points / scale formula would get backwards) via ReferenceDataRequest field
    PX_LAST, plus each tenor's settlement date from field SETTLE_DT (or FWD_SETTLE_DT)
    on the same ticker. Outright = spot + points / scale; scale is read per pair from
    field FWD_POINTS_SCALE (a.k.a. the "points divisor") on the '<PAIR> Curncy' ticker
    rather than hard-coded, since it varies by pair (e.g. 100 for JPY-style pairs, 10000
    for others). Rows whose target settle_date falls before the first tenor (SP) or
    after the last tenor are rejected/logged, never extrapolated. Interpolation is
    linear in forward points (not in outright levels) between the two bracketing tenor
    dates.

Diagnostics:
  Every Bloomberg request made (ReferenceDataRequest or HistoricalDataRequest) is
  recorded: request id, request type, tickers, fields, overrides, start/end time, the
  full raw response (every field value, every fieldException with its message, every
  securityError, and any TIMEOUT), and a classification in
  {OK, NO_VALUE, FIELD_EXCEPTION, SECURITY_ERROR, TIMEOUT, SESSION_ERROR}. FWD_OUTRIGHT
  tenor-fallback requests additionally get an "interpolations" entry recording the
  tenor points, settle dates, scale, and the interpolation inputs/result; every
  FWD_OUTRIGHT request (direct or fallback) also gets an entry in "fwd_outright_results"
  recording whether it was resolved directly, via BBG_INTERP, or failed. The JSON top-
  level structure is: environment, requests, interpolations, fwd_outright_results, run,
  summary, exception (null unless an unhandled exception occurred). Read it with
  data/bloomberg/pull_report.py, which prints a plain-text report and exits non-zero if any
  failure is present.

--probe mode: runs a fixed sequence of exploratory requests (session start; open
//blp/refdata; EURUSD PX_LAST via ReferenceDataRequest and HistoricalDataRequest; the
direct broken-date FWD_OUTRIGHT request plus two alternative candidates; the EURUSD1M
and EURUSD3M tenor tickers; FWD_POINTS_SCALE; PX_SETTLE/PX_LAST on ESU6 Index) and
records every result in the same diagnostics JSON, tagged with a probe_name and (where
relevant) which numbered open-questions item it answers. Each step failing never aborts
the remaining steps. No marks CSV is written or needed in probe mode.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import sys
import traceback
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Mirrors data.bloomberg.marks_csv.MARKS_COLUMNS. Duplicated (not imported) because this
# script must be copyable as a single file with no repo imports.
MARKS_COLUMNS = ["as_of_date", "instrument_id", "settle_date", "mark_type", "value", "source", "snapped_at"]

REQUEST_COLUMNS = ["instrument_id", "bbg_ticker", "settle_date", "mark_type"]

SRC_SPOT_FWD = "BBG_BFXFORWARD"
SRC_FUTURE = "BBG_BDH"
SRC_INTERP = "BBG_INTERP"

_NY_ZONE: Optional[ZoneInfo] = None


def _ny() -> ZoneInfo:
    """Lazily resolve and cache the America/New_York zoneinfo. This must NOT be a
    module-level constant: constructing ZoneInfo("America/New_York") raises
    ZoneInfoNotFoundError immediately wherever the IANA tz database isn't available --
    notably stock Windows Python without the `tzdata` pip package -- and a module-level
    constant would raise that before argparse even runs, so no diag JSON would ever be
    written for the failure. main() resolves this explicitly, immediately after
    Diagnostics() is created and before the first record_environment() call, and turns a
    missing zone (ZoneInfoNotFoundError) into a diag entry (failure with
    stage "tz_prerequisite" and a "py -3 -m pip install tzdata" hint), a diag JSON write,
    a matching stderr hint, and exit 6 -- see module docstring exit-code table."""
    global _NY_ZONE
    if _NY_ZONE is None:
        _NY_ZONE = ZoneInfo("America/New_York")
    return _NY_ZONE

# ON/TN deliberately excluded: their settle dates are before spot (SP) and their points
# use the pre-spot quoting convention (e.g. outright = spot - points for TN), which
# outright_from_points()'s spot + points/scale would apply incorrectly. See module
# docstring "FWD_OUTRIGHT fallback".
STANDARD_TENORS = ["SP", "1W", "2W", "1M", "2M", "3M", "6M", "1Y"]

# nextEvent() timeout so a stalled/unreachable session doesn't hang forever.
EVENT_TIMEOUT_MS = 30000

# Request/probe-step classifications recorded in the diagnostics JSON.
CLASS_OK = "OK"
CLASS_NO_VALUE = "NO_VALUE"
CLASS_FIELD_EXCEPTION = "FIELD_EXCEPTION"
CLASS_SECURITY_ERROR = "SECURITY_ERROR"
CLASS_TIMEOUT = "TIMEOUT"
CLASS_SESSION_ERROR = "SESSION_ERROR"
CLASS_EXCEPTION = "EXCEPTION"
# A row rejected by our own logic (settle_date outside the standard-tenor range, before
# spot, etc) rather than by anything Bloomberg reported -- distinct from CLASS_NO_VALUE
# (Bloomberg had nothing) so pull_report.py can tell "we didn't ask for something sane" from
# "Bloomberg came back empty".
CLASS_REJECTED = "REJECTED"

_FAILURE_CLASSIFICATIONS = {
    CLASS_FIELD_EXCEPTION, CLASS_SECURITY_ERROR, CLASS_TIMEOUT, CLASS_SESSION_ERROR, CLASS_EXCEPTION,
}

_REQUEST_COUNTER = itertools.count(1)
# Correlation ids for blpapi requests (see W-1: discard/record any message whose
# correlationIds() don't match the request that's currently being waited on, so a late
# response to a previous (e.g. timed-out) request can never pollute the next request).
_CORRELATION_COUNTER = itertools.count(1)


def snapped_at(as_of: date) -> str:
    """17:00 America/New_York on as_of, ISO with the resolved offset for that date."""
    return datetime(as_of.year, as_of.month, as_of.day, 17, 0, 0, tzinfo=_ny()).isoformat()


class StaleAsOfError(RuntimeError):
    """Raised when --as-of is not today (America/New_York) and the request includes
    FWD_OUTRIGHT rows, without --allow-stale-as-of. See module docstring
    "Staleness guard"."""


class BloombergRequestError(RuntimeError):
    """A Bloomberg request could not be answered at all (currently: TIMEOUT). Carries the
    classification/detail already recorded into the diagnostics log by the fetch_*
    function that raised it, so callers only need to catch it and decide what to do next
    (run() catches per mark-type stage and continues with the remaining stages, marking
    every requested row of that stage as failed -- see run()'s docstring and the module
    docstring's "partial" definition)."""

    def __init__(self, request_type: str, tickers: Sequence[str], fields: Sequence[str],
                 classification: str, detail: str):
        super().__init__(f"{request_type} {tickers} {fields}: {classification}: {detail}")
        self.request_type = request_type
        self.tickers = list(tickers)
        self.fields = list(fields)
        self.classification = classification
        self.detail = detail


def check_not_stale(as_of: date, requests: Sequence["RequestRow"], allow_stale: bool,
                     today: Optional[date] = None) -> None:
    """FWD_OUTRIGHT pulls (direct + tenor fallback) are live ReferenceDataRequest calls:
    they reflect the market at the moment the script runs, not the close of --as-of. If
    --as-of != today (America/New_York) and the request has any FWD_OUTRIGHT rows,
    refuse to run unless allow_stale is set, in which case log a warning and proceed.
    SPOT and FUTURE_PX are HistoricalDataRequest for as_of and are unaffected either way.
    """
    if not any(r.mark_type == "FWD_OUTRIGHT" for r in requests):
        return
    today = today if today is not None else datetime.now(_ny()).date()
    if as_of == today:
        return
    msg = (
        f"--as-of {as_of.isoformat()} is not today ({today.isoformat()} America/New_York); "
        f"FWD_OUTRIGHT rows are pulled live and would be mislabeled as {as_of.isoformat()}'s "
        f"close/snapped_at."
    )
    if not allow_stale:
        raise StaleAsOfError(msg + " Pass --allow-stale-as-of to override.")
    print(f"WARNING: {msg} Proceeding because --allow-stale-as-of was passed.", file=sys.stderr)


# --------------------------------------------------------------------------- diagnostics
def _now_ny_iso() -> str:
    return datetime.now(_ny()).isoformat()


def _hostname() -> Optional[str]:
    try:
        import socket
        return socket.gethostname()
    except Exception:
        return None


class Diagnostics:
    """Collects a full record of every Bloomberg request made during a pull or probe run,
    the environment, and a final summary, written to "<out>.diag.json" (see write_diagnostics).
    See the module docstring "Diagnostics" section for the JSON shape."""

    def __init__(self):
        self.environment: Dict[str, object] = {}
        self.requests: List[dict] = []
        self.interpolations: List[dict] = []
        self.fwd_outright_results: List[dict] = []
        self.run_info: Dict[str, object] = {}
        self.summary: Dict[str, object] = {}
        self.exception: Optional[dict] = None

    def record_environment(self, host, port, session_started=None, service_opened=None) -> None:
        try:
            blpapi = _get_blpapi()
            blpapi_version = getattr(blpapi, "__version__", "unknown")
        except Exception as e:
            blpapi_version = f"unavailable ({e})"
        self.environment = {
            "python_version": sys.version,
            "blpapi_version": blpapi_version,
            "hostname": _hostname(),
            "host": host,
            "port": port,
            "session_started": session_started,
            "service_opened": service_opened,
            "machine_time_local": datetime.now().isoformat(),
            "machine_time_america_new_york": datetime.now(_ny()).isoformat(),
            "machine_timezone": str(datetime.now().astimezone().tzinfo),
        }

    def new_request(self, request_type: str, tickers: Sequence[str], fields: Sequence[str],
                     overrides: Optional[Dict[str, str]] = None, tag: Optional[dict] = None) -> dict:
        rec = {
            "request_id": next(_REQUEST_COUNTER),
            "request_type": request_type,
            "tickers": list(tickers),
            "fields": list(fields),
            "overrides": dict(overrides) if overrides else {},
            "start_time": _now_ny_iso(),
            "end_time": None,
            "events": [],
            "raw_response": [],
            "classification": None,
            "detail": None,
        }
        if tag:
            rec.update(tag)
        self.requests.append(rec)
        return rec

    def finish_request(self, rec: dict, classification: str, detail: Optional[str]) -> None:
        rec["end_time"] = _now_ny_iso()
        rec["classification"] = classification
        rec["detail"] = detail

    def record_synthetic(self, request_type: str, classification: str, detail: Optional[str],
                          tag: Optional[dict] = None) -> dict:
        """For diagnostics entries that aren't a fetch_reference/fetch_historical call
        (session start, service open, a probe step that raised before reaching a fetch_*
        call)."""
        rec = self.new_request(request_type, [], [], None, tag)
        self.finish_request(rec, classification, detail)
        return rec

    def record_interpolation(self, pair: str, target_settle_date: str, tenor_points: Sequence["TenorPoint"],
                              scale: Optional[float], interp_points: Optional[float],
                              outright: Optional[float], spot: Optional[float]) -> None:
        self.interpolations.append({
            "pair": pair,
            "target_settle_date": target_settle_date,
            "tenor_points": [
                {"tenor": p.tenor, "settle_date": p.settle_date.isoformat(), "points": p.points}
                for p in tenor_points
            ],
            "scale": scale,
            "spot": spot,
            "interpolated_points": interp_points,
            "outright": outright,
        })

    def record_fwd_outright_result(self, instrument_id: str, settle_date: str, outcome: str,
                                    source: Optional[str] = None, value: Optional[float] = None,
                                    detail: Optional[str] = None) -> None:
        self.fwd_outright_results.append({
            "instrument_id": instrument_id, "settle_date": settle_date, "outcome": outcome,
            "source": source, "value": value, "detail": detail,
        })

    def record_exception(self, exc: BaseException) -> None:
        self.exception = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }

    def finalize_summary(self, exit_code: int, failures: List[dict], marks_csv_partial: bool,
                          requested_rows: Optional[int] = None, written_rows: Optional[int] = None,
                          mode: str = "pull", outcome: Optional[str] = None) -> None:
        """`outcome` defaults to "OK"/"FAILED" derived from exit_code, but callers may pass
        an explicit value (e.g. probe mode always exits 0 but must not claim "OK" when
        some non-candidate step failed -- see run_probe(), which passes
        "PROBE_COMPLETE_WITH_FAILURES")."""
        counts: Dict[str, int] = {}
        for r in self.requests:
            c = r.get("classification") or "UNKNOWN"
            counts[c] = counts.get(c, 0) + 1
        self.summary = {
            "mode": mode,
            "outcome": outcome if outcome is not None else ("OK" if exit_code == 0 else "FAILED"),
            "exit_code": exit_code,
            "failures": failures,
            "marks_csv_partial": marks_csv_partial,
            "requested_rows": requested_rows,
            "written_rows": written_rows,
            "counts_by_classification": counts,
        }

    def to_dict(self) -> dict:
        return {
            "environment": self.environment,
            "requests": self.requests,
            "interpolations": self.interpolations,
            "fwd_outright_results": self.fwd_outright_results,
            "run": self.run_info,
            "summary": self.summary,
            "exception": self.exception,
        }


def _sanitize_for_json(obj):
    """json.dump writes bare NaN/Infinity tokens (invalid JSON) for float('nan')/inf by
    default. Bloomberg field values or interpolation results could plausibly be NaN/inf
    (e.g. a divide-by-zero in a badly-scaled points calc); walk the structure and turn
    them into strings so the diagnostics file is always valid JSON."""
    if isinstance(obj, float):
        if obj != obj:  # NaN
            return "NaN"
        if obj == float("inf"):
            return "inf"
        if obj == float("-inf"):
            return "-inf"
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    return obj


def write_diagnostics(diag: Diagnostics, path) -> None:
    """Never let a diagnostics-writing failure mask the run's real exit code or lose the
    diag entirely: create the output directory if missing, and if writing still fails for
    any reason, report it to stderr instead of raising out of main()'s finally block."""
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(_sanitize_for_json(diag.to_dict()), f, indent=2, default=str)
    except Exception as e:
        print(f"ERROR: failed to write diagnostics to {path}: {type(e).__name__}: {e}", file=sys.stderr)


def diag_path_for(out_path) -> Path:
    return Path(str(out_path) + ".diag.json")


# --------------------------------------------------------------------------- network layer
def _get_blpapi():
    """Import blpapi lazily so a fake module injected into sys.modules['blpapi'] before
    this is called (e.g. by a test) is picked up, and so importing this module for its
    pure functions (interpolation, CSV assembly) never requires blpapi to be installed.
    """
    import blpapi  # noqa: F401 (imported for side effect / availability check)
    return blpapi


def open_session(host: str = "localhost", port: int = 8194, diag: Optional[Diagnostics] = None):
    """Open a blpapi Session and start the //blp/refdata service. Returns (session, service).

    If `diag` is given, the environment block is updated in two phases (session_started,
    then service_opened) so a diag written after a failure here distinguishes "the
    session itself never started" from "the session started but //blp/refdata wouldn't
    open" (W-2) -- previously both raised the same generic RuntimeError and callers
    always recorded (False, False).
    """
    blpapi = _get_blpapi()
    opts = blpapi.SessionOptions()
    opts.setServerHost(host)
    opts.setServerPort(port)
    session = blpapi.Session(opts)
    if not session.start():
        if diag is not None:
            diag.record_environment(host, port, session_started=False, service_opened=False)
        raise RuntimeError(f"failed to start blpapi session at {host}:{port}")
    if diag is not None:
        diag.record_environment(host, port, session_started=True, service_opened=None)
    if not session.openService("//blp/refdata"):
        if diag is not None:
            diag.record_environment(host, port, session_started=True, service_opened=False)
        raise RuntimeError("failed to open //blp/refdata")
    service = session.getService("//blp/refdata")
    if diag is not None:
        diag.record_environment(host, port, session_started=True, service_opened=True)
    return session, service


def _parse_ref_field_data(fd, fields: Sequence[str]) -> dict:
    row = {}
    for f in fields:
        if fd.hasElement(f):
            row[f] = fd.getElement(f).getValue()
    return row


def _parse_field_exceptions(sd) -> List[dict]:
    out: List[dict] = []
    if sd.hasElement("fieldExceptions"):
        fx_el = sd.getElement("fieldExceptions")
        for j in range(fx_el.numValues()):
            fx = fx_el.getValueAsElement(j)
            field_id = fx.getElementAsString("fieldId") if fx.hasElement("fieldId") else None
            message = None
            if fx.hasElement("errorInfo"):
                ei = fx.getElement("errorInfo")
                if ei.hasElement("message"):
                    message = ei.getElementAsString("message")
            out.append({"fieldId": field_id, "message": message})
    return out


def _parse_security_error(sd) -> Optional[dict]:
    if sd.hasElement("securityError"):
        se = sd.getElement("securityError")
        return {"message": se.getElementAsString("message") if se.hasElement("message") else None}
    return None


def _classify_secs(raw_secs: List[dict], timed_out: bool, expected_tickers: Sequence[str]) -> Tuple[str, Optional[str]]:
    """Priority: TIMEOUT > SECURITY_ERROR > FIELD_EXCEPTION > NO_VALUE > OK.

    This classifies the whole batched request (e.g. all 8 standard-tenor tickers in one
    fetch_tenor_points() call), not each ticker individually: one ticker's fieldException
    marks the whole request record FIELD_EXCEPTION even if the row(s) that actually
    needed that ticker were written fine from the others. Per-ticker detail is still
    available in raw_response (fieldExceptions/securityError are recorded per security),
    and whether a FIELD_EXCEPTION on one ticker actually mattered is decided downstream:
    run_pull() reconciles the full requested-key set against the written rows and only
    ever reports a row in summary.failures if it was NOT written, regardless of what this
    function classified the containing batch as (see W-6).
    """
    if timed_out:
        got = [s["security"] for s in raw_secs]
        missing = [t for t in expected_tickers if t not in got]
        return CLASS_TIMEOUT, f"nextEvent timed out after {EVENT_TIMEOUT_MS}ms; got {got}, missing {missing}"
    if any(s["securityError"] for s in raw_secs):
        bad = [s["security"] for s in raw_secs if s["securityError"]]
        return CLASS_SECURITY_ERROR, f"securityError on: {bad}"
    if any(s["fieldExceptions"] for s in raw_secs):
        bad = [s["security"] for s in raw_secs if s["fieldExceptions"]]
        return CLASS_FIELD_EXCEPTION, f"fieldExceptions on: {bad}"
    if not raw_secs:
        return CLASS_NO_VALUE, "no securityData returned"
    if any(not s["fieldData"] for s in raw_secs):
        missing = [s["security"] for s in raw_secs if not s["fieldData"]]
        return CLASS_NO_VALUE, f"no fieldData for: {missing}"
    return CLASS_OK, None


def fetch_reference(session, service, tickers: Sequence[str], fields: Sequence[str],
                     overrides: Optional[Dict[str, str]] = None,
                     diag: Optional[Diagnostics] = None, tag: Optional[dict] = None) -> Dict[str, Dict[str, object]]:
    """Thin network layer: ReferenceDataRequest for `tickers` x `fields`, with optional
    field overrides (e.g. SETTLE_DT). Returns {ticker: {field: value}}, plain dicts only
    (no blpapi objects escape this function), so callers and tests never touch blpapi
    types directly.

    A field missing from the response (blpapi field-not-applicable / error) is simply
    absent from that ticker's dict rather than raising -- callers decide whether that
    means "fall back" or "reject". If `diag` is given, every call is recorded (tickers,
    fields, overrides, timing, the full raw response including fieldExceptions and
    securityError, and a classification -- see Diagnostics). A TIMEOUT is recorded and
    then raised as BloombergRequestError (never silently returns partial data as if the
    request had succeeded).

    Every request is sent with its own blpapi.CorrelationId; any message received whose
    correlationIds() don't include it is a late/stale response belonging to a previous
    (e.g. timed-out) request and is discarded rather than merged into `out` -- it is
    still recorded, under this request's "late_responses" diag entry, for debugging (W-1).
    """
    request = service.createRequest("ReferenceDataRequest")
    for t in tickers:
        request.getElement("securities").appendValue(t)
    for f in fields:
        request.getElement("fields").appendValue(f)
    if overrides:
        ov = request.getElement("overrides")
        for name, value in overrides.items():
            o = ov.appendElement()
            o.setElement("fieldId", name)
            o.setElement("value", value)

    rec = diag.new_request("ReferenceDataRequest", tickers, fields, overrides, tag) if diag is not None else None

    blpapi = _get_blpapi()
    correlation_id = blpapi.CorrelationId(next(_CORRELATION_COUNTER))
    session.sendRequest(request, correlationId=correlation_id)
    out: Dict[str, Dict[str, object]] = {}
    raw_secs: List[dict] = []
    late_responses: List[dict] = []
    timed_out = False
    while True:
        event = session.nextEvent(EVENT_TIMEOUT_MS)
        event_type = event.eventType()
        if rec is not None:
            rec["events"].append(str(event_type))
        if event_type == getattr(blpapi.Event, "TIMEOUT", None):
            timed_out = True
            print(f"WARNING: nextEvent timed out after {EVENT_TIMEOUT_MS}ms waiting for "
                  f"ReferenceDataRequest response", file=sys.stderr)
            break
        event_is_ours = False
        for msg in event:
            msg_cids = list(msg.correlationIds()) if hasattr(msg, "correlationIds") else []
            if msg_cids and correlation_id not in msg_cids:
                late_responses.append({
                    "reason": "correlationId mismatch: message belongs to a previous request, discarded",
                    "expected": str(correlation_id), "got": [str(c) for c in msg_cids],
                })
                continue
            event_is_ours = True
            if not msg.hasElement("securityData"):
                continue
            sec_data = msg.getElement("securityData")
            for i in range(sec_data.numValues()):
                sd = sec_data.getValueAsElement(i)
                ticker = sd.getElementAsString("security")
                row: Dict[str, object] = {}
                if sd.hasElement("fieldData"):
                    fd = sd.getElement("fieldData")
                    row = _parse_ref_field_data(fd, fields)
                out[ticker] = row
                raw_secs.append({
                    "security": ticker, "fieldData": row,
                    "fieldExceptions": _parse_field_exceptions(sd),
                    "securityError": _parse_security_error(sd),
                })
        if event_type == blpapi.Event.RESPONSE and event_is_ours:
            break

    classification, detail = _classify_secs(raw_secs, timed_out, tickers)
    if rec is not None:
        rec["raw_response"] = raw_secs
        rec["late_responses"] = late_responses
        diag.finish_request(rec, classification, detail)
    if classification == CLASS_TIMEOUT:
        raise BloombergRequestError("ReferenceDataRequest", tickers, fields, classification, detail)
    return out


def fetch_historical(session, service, tickers: Sequence[str], field: str, as_of: date,
                      diag: Optional[Diagnostics] = None, tag: Optional[dict] = None) -> Dict[str, Optional[float]]:
    """Thin network layer: HistoricalDataRequest for a single date (start = end = as_of).
    Returns {ticker: value or None}. Same diagnostics/TIMEOUT/correlation-id behaviour as
    fetch_reference (see its docstring)."""
    request = service.createRequest("HistoricalDataRequest")
    for t in tickers:
        request.getElement("securities").appendValue(t)
    request.getElement("fields").appendValue(field)
    d = as_of.strftime("%Y%m%d")
    request.set("startDate", d)
    request.set("endDate", d)

    rec = diag.new_request("HistoricalDataRequest", tickers, [field], None, tag) if diag is not None else None

    blpapi = _get_blpapi()
    correlation_id = blpapi.CorrelationId(next(_CORRELATION_COUNTER))
    session.sendRequest(request, correlationId=correlation_id)
    out: Dict[str, Optional[float]] = {}
    raw_secs: List[dict] = []
    late_responses: List[dict] = []
    timed_out = False
    while True:
        event = session.nextEvent(EVENT_TIMEOUT_MS)
        event_type = event.eventType()
        if rec is not None:
            rec["events"].append(str(event_type))
        if event_type == getattr(blpapi.Event, "TIMEOUT", None):
            timed_out = True
            print(f"WARNING: nextEvent timed out after {EVENT_TIMEOUT_MS}ms waiting for "
                  f"HistoricalDataRequest response", file=sys.stderr)
            break
        event_is_ours = False
        for msg in event:
            msg_cids = list(msg.correlationIds()) if hasattr(msg, "correlationIds") else []
            if msg_cids and correlation_id not in msg_cids:
                late_responses.append({
                    "reason": "correlationId mismatch: message belongs to a previous request, discarded",
                    "expected": str(correlation_id), "got": [str(c) for c in msg_cids],
                })
                continue
            event_is_ours = True
            if not msg.hasElement("securityData"):
                continue
            sec_data = msg.getElement("securityData")
            ticker = sec_data.getElementAsString("security")
            value = None
            field_data_repr: Dict[str, object] = {}
            if sec_data.hasElement("fieldData"):
                fd = sec_data.getElement("fieldData")
                if fd.numValues() > 0:
                    point = fd.getValueAsElement(0)
                    if point.hasElement(field):
                        value = point.getElement(field).getValue()
                        field_data_repr = {field: value}
            out[ticker] = value
            raw_secs.append({
                "security": ticker, "fieldData": field_data_repr,
                "fieldExceptions": _parse_field_exceptions(sec_data),
                "securityError": _parse_security_error(sec_data),
            })
        if event_type == blpapi.Event.RESPONSE and event_is_ours:
            break

    classification, detail = _classify_secs(raw_secs, timed_out, tickers)
    if rec is not None:
        rec["raw_response"] = raw_secs
        rec["late_responses"] = late_responses
        diag.finish_request(rec, classification, detail)
    if classification == CLASS_TIMEOUT:
        raise BloombergRequestError("HistoricalDataRequest", tickers, [field], classification, detail)
    return out


# --------------------------------------------------------------------------- pure logic
@dataclass(frozen=True)
class TenorPoint:
    tenor: str
    settle_date: date
    points: float


def interpolate_forward_points(target: date, tenor_points: Sequence[TenorPoint]) -> Optional[float]:
    """Linear interpolation in forward points between the two tenor dates bracketing
    `target`. Returns None if `target` is before the first tenor date or after the last
    (never extrapolates) or if fewer than 2 tenor points are given.

    Pure function: no network, no blpapi.
    """
    pts = sorted(tenor_points, key=lambda p: p.settle_date)
    if len(pts) < 2:
        return None
    if target < pts[0].settle_date or target > pts[-1].settle_date:
        return None
    if target == pts[0].settle_date:
        return pts[0].points
    for a, b in zip(pts, pts[1:]):
        if a.settle_date <= target <= b.settle_date:
            if b.settle_date == a.settle_date:
                return a.points
            frac = (target - a.settle_date).days / (b.settle_date - a.settle_date).days
            return a.points + frac * (b.points - a.points)
    return None  # pragma: no cover (unreachable given the bounds check above)


def outright_from_points(spot: float, points: float, scale: float) -> float:
    """outright = spot + points / scale (CLAUDE.md gives no formula; this is the
    standard FX forward-points convention: points are quoted in pips-of-the-pair,
    scale = FWD_POINTS_SCALE for that pair, e.g. 100 for JPY-style pairs)."""
    return spot + points / scale


# --------------------------------------------------------------------------- request CSV
@dataclass(frozen=True)
class RequestRow:
    instrument_id: str
    bbg_ticker: str
    settle_date: str
    mark_type: str


def read_request_csv(path) -> List[RequestRow]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [RequestRow(r["instrument_id"], r["bbg_ticker"], r["settle_date"], r["mark_type"]) for r in reader]


# --------------------------------------------------------------------------- assembly
def _batch_classification(diag: Optional[Diagnostics]) -> Tuple[Optional[str], Optional[str]]:
    """Classification/detail of the most recently recorded diag request, used as a
    best-effort per-row classification when a batched request comes back OK overall but
    is missing one ticker's value (see build_spot_rows/build_future_rows)."""
    if diag is None or not diag.requests:
        return None, None
    rec = diag.requests[-1]
    return rec.get("classification"), rec.get("detail")


def build_spot_rows(session, service, requests: Sequence[RequestRow], as_of: date,
                     diag: Optional[Diagnostics] = None) -> Tuple[List[dict], List[str], List[dict]]:
    """SPOT: HistoricalDataRequest PX_LAST for as_of (consistent with FUTURE_PX -- both
    read the as_of close, not a live quote, so snapped_at is never mislabeled regardless
    of when the script actually runs). See module docstring.

    Returns (rows, warnings, failures) -- `failures` has one entry per requested SPOT row
    that was NOT written, each with its own classification/detail (see C-A: a partial
    marks CSV is now defined purely by comparing requested vs written keys, so every skip
    here must be visible, not just silently folded into a warning)."""
    rows = [r for r in requests if r.mark_type == "SPOT"]
    if not rows:
        return [], [], []
    tickers = sorted({r.bbg_ticker for r in rows})
    data = fetch_historical(session, service, tickers, "PX_LAST", as_of, diag, {"purpose": "SPOT"})
    batch_classification, batch_detail = _batch_classification(diag)
    out, warnings, failures = [], [], []
    snapped = snapped_at(as_of)
    for r in rows:
        val = data.get(r.bbg_ticker)
        if val is None:
            detail = f"no PX_LAST returned for {as_of}" + (f" ({batch_detail})" if batch_detail else "")
            warnings.append(f"SPOT {r.instrument_id} ({r.bbg_ticker}): {detail}")
            classification = batch_classification if batch_classification and batch_classification != CLASS_OK else CLASS_NO_VALUE
            failures.append({
                "instrument_id": r.instrument_id, "settle_date": r.settle_date, "mark_type": "SPOT",
                "classification": classification, "detail": detail,
            })
            continue
        out.append({
            "as_of_date": as_of.isoformat(), "instrument_id": r.instrument_id,
            "settle_date": r.settle_date, "mark_type": "SPOT", "value": float(val),
            "source": SRC_SPOT_FWD, "snapped_at": snapped,
        })
    return out, warnings, failures


def build_future_rows(session, service, requests: Sequence[RequestRow], as_of: date,
                       diag: Optional[Diagnostics] = None) -> Tuple[List[dict], List[str], List[dict]]:
    """FUTURE_PX: HistoricalDataRequest PX_SETTLE for as_of. See module docstring and
    build_spot_rows for the (rows, warnings, failures) contract."""
    rows = [r for r in requests if r.mark_type == "FUTURE_PX"]
    if not rows:
        return [], [], []
    tickers = sorted({r.bbg_ticker for r in rows})
    data = fetch_historical(session, service, tickers, "PX_SETTLE", as_of, diag, {"purpose": "FUTURE_PX"})
    batch_classification, batch_detail = _batch_classification(diag)
    out, warnings, failures = [], [], []
    snapped = snapped_at(as_of)
    for r in rows:
        val = data.get(r.bbg_ticker)
        if val is None:
            detail = f"no PX_SETTLE returned for {as_of}" + (f" ({batch_detail})" if batch_detail else "")
            warnings.append(f"FUTURE_PX {r.instrument_id} ({r.bbg_ticker}): {detail}")
            classification = batch_classification if batch_classification and batch_classification != CLASS_OK else CLASS_NO_VALUE
            failures.append({
                "instrument_id": r.instrument_id, "settle_date": r.settle_date, "mark_type": "FUTURE_PX",
                "classification": classification, "detail": detail,
            })
            continue
        out.append({
            "as_of_date": as_of.isoformat(), "instrument_id": r.instrument_id,
            "settle_date": r.settle_date, "mark_type": "FUTURE_PX", "value": float(val),
            "source": SRC_FUTURE, "snapped_at": snapped,
        })
    return out, warnings, failures


def fetch_fwd_outright_direct(session, service, bbg_ticker: str, settle_date: str,
                               warnings: Optional[List[str]] = None,
                               diag: Optional[Diagnostics] = None, tag: Optional[dict] = None) -> Optional[float]:
    """UNVERIFIED: attempt a direct broken-date outright via ReferenceDataRequest on
    field FWD_CURVE with overrides FWD_CURVE_QUOTE_FORMAT='OUTRIGHTS' and
    SETTLE_DT=<settle_date, YYYYMMDD>. Returns None if the field is absent from the
    response, or if it comes back as something other than a plain number (on a real
    terminal FWD_CURVE is a bulk field -- a list of rows -- when the override doesn't
    pin it to a single scalar; float(list) would raise TypeError and kill the run, so
    that case is treated as "no direct value" and logged as a warning instead). The
    fallback path in build_fwd_outright_rows then applies either way.
    """
    data = fetch_reference(
        session, service, [bbg_ticker], ["FWD_CURVE"],
        overrides={"FWD_CURVE_QUOTE_FORMAT": "OUTRIGHTS",
                   "SETTLE_DT": settle_date.replace("-", "")},
        diag=diag, tag=tag,
    )
    val = data.get(bbg_ticker, {}).get("FWD_CURVE")
    if val is None:
        return None
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        if warnings is not None:
            warnings.append(
                f"FWD_OUTRIGHT direct {bbg_ticker} settle {settle_date}: FWD_CURVE returned "
                f"a non-scalar value ({type(val).__name__}), falling back to tenor interpolation")
        return None
    return float(val)


def fetch_tenor_points(session, service, pair: str,
                        diag: Optional[Diagnostics] = None) -> Tuple[List[TenorPoint], Optional[float]]:
    """Fetch standard-tenor forward points (PX_LAST) and settlement dates (SETTLE_DT)
    for '<pair><TENOR> Curncy', plus the pair's FWD_POINTS_SCALE from '<pair> Curncy'.
    Tenors whose response is missing either field are skipped, not defaulted.
    """
    tenor_tickers = {tenor: f"{pair}{tenor} Curncy" for tenor in STANDARD_TENORS}
    data = fetch_reference(session, service, list(tenor_tickers.values()), ["PX_LAST", "SETTLE_DT"],
                            diag=diag, tag={"purpose": "tenor_fallback", "pair": pair})
    points: List[TenorPoint] = []
    for tenor, ticker in tenor_tickers.items():
        row = data.get(ticker, {})
        if "PX_LAST" not in row or "SETTLE_DT" not in row:
            continue
        sd = row["SETTLE_DT"]
        if isinstance(sd, str):
            sd = datetime.strptime(sd, "%Y-%m-%d").date() if "-" in sd else datetime.strptime(sd, "%Y%m%d").date()
        elif hasattr(sd, "isoformat") and not isinstance(sd, date):
            sd = sd.date()
        points.append(TenorPoint(tenor, sd, float(row["PX_LAST"])))

    scale_data = fetch_reference(session, service, [f"{pair} Curncy"], ["FWD_POINTS_SCALE"],
                                  diag=diag, tag={"purpose": "tenor_fallback_scale", "pair": pair})
    scale_val = scale_data.get(f"{pair} Curncy", {}).get("FWD_POINTS_SCALE")
    scale = float(scale_val) if scale_val is not None else None
    return points, scale


def build_fwd_outright_rows(session, service, requests: Sequence[RequestRow], as_of: date,
                             spot_rows_by_instrument: Dict[str, float],
                             diag: Optional[Diagnostics] = None,
                             spot_batch_classification: Optional[str] = None,
                             spot_batch_detail: Optional[str] = None,
                             ) -> Tuple[List[dict], List[str], List[dict]]:
    """FWD_OUTRIGHT: try the direct broken-date request first; fall back to standard-
    tenor forward-point interpolation. See module docstring for field/override names.

    Returns (rows, warnings, failures) -- see build_spot_rows for the contract. Each row
    is handled independently: a BloombergRequestError (TIMEOUT) on one row's direct or
    tenor-fallback request is caught here (not just at the run()-level stage boundary) so
    a stalled request for one settle_date doesn't discard rows already resolved earlier in
    this same loop.

    `spot_batch_classification`/`spot_batch_detail` (W-2) are the classification/detail of
    the SPOT stage's own batched request (as captured by run() right after build_spot_rows
    returns, via _batch_classification) -- when a row here has no SPOT to interpolate off
    of, the failure this function records uses that underlying classification (e.g.
    SECURITY_ERROR/TIMEOUT) instead of always hard-coding CLASS_NO_VALUE, which previously
    hid the real reason SPOT was missing. Same idea for FWD_POINTS_SCALE: the scale
    request's own classification is captured right after it is made and cached alongside
    the tenor points, so a scale-missing failure reflects why (e.g. FIELD_EXCEPTION) rather
    than a blanket NO_VALUE."""
    rows = [r for r in requests if r.mark_type == "FWD_OUTRIGHT"]
    if not rows:
        return [], [], []
    out, warnings, failures = [], [], []
    snapped = snapped_at(as_of)
    tenor_cache: Dict[str, Tuple[List[TenorPoint], Optional[float], Optional[str], Optional[str]]] = {}

    def _fail(detail: str, classification: str) -> None:
        warnings.append(f"FWD_OUTRIGHT {pair} {r.settle_date}: {detail}")
        failures.append({
            "instrument_id": r.instrument_id, "settle_date": r.settle_date, "mark_type": "FWD_OUTRIGHT",
            "classification": classification, "detail": detail,
        })
        if diag is not None:
            diag.record_fwd_outright_result(r.instrument_id, r.settle_date, "failed", detail=detail)

    for r in rows:
        pair = r.instrument_id
        target = date.fromisoformat(r.settle_date)

        try:
            direct = fetch_fwd_outright_direct(
                session, service, r.bbg_ticker, r.settle_date, warnings, diag=diag,
                tag={"purpose": "fwd_outright_direct", "instrument_id": r.instrument_id,
                     "settle_date": r.settle_date},
            )
        except BloombergRequestError as e:
            _fail(f"direct request failed: {e}", e.classification)
            continue
        if direct is not None:
            out.append({
                "as_of_date": as_of.isoformat(), "instrument_id": r.instrument_id,
                "settle_date": r.settle_date, "mark_type": "FWD_OUTRIGHT", "value": direct,
                "source": SRC_SPOT_FWD, "snapped_at": snapped,
            })
            if diag is not None:
                diag.record_fwd_outright_result(r.instrument_id, r.settle_date, "direct",
                                                 source=SRC_SPOT_FWD, value=direct)
            continue

        # fallback: tenor interpolation
        if pair not in tenor_cache:
            try:
                t_points, t_scale = fetch_tenor_points(session, service, pair, diag=diag)
            except BloombergRequestError as e:
                _fail(f"tenor fallback request failed: {e}", e.classification)
                continue
            # fetch_tenor_points' last request is the FWD_POINTS_SCALE lookup, so the most
            # recently recorded diag entry is that request's own classification/detail.
            scale_classification, scale_detail = _batch_classification(diag)
            tenor_cache[pair] = (t_points, t_scale, scale_classification, scale_detail)
        points, scale, scale_classification, scale_detail = tenor_cache[pair]
        spot = spot_rows_by_instrument.get(pair)
        if spot is None:
            classification = (spot_batch_classification
                               if spot_batch_classification and spot_batch_classification != CLASS_OK
                               else CLASS_NO_VALUE)
            detail = "no direct value and no SPOT to base interpolation on"
            if spot_batch_detail:
                detail += f" ({spot_batch_detail})"
            _fail(detail, classification)
            continue
        if scale is None:
            classification = (scale_classification
                               if scale_classification and scale_classification != CLASS_OK
                               else CLASS_NO_VALUE)
            detail = "no FWD_POINTS_SCALE returned, cannot interpolate"
            if scale_detail:
                detail += f" ({scale_detail})"
            _fail(detail, classification)
            continue
        interp_points = interpolate_forward_points(target, points)
        if interp_points is None:
            if points and target < points[0].settle_date:
                detail = (
                    f"settle_date is before spot ({points[0].settle_date.isoformat()}), rejected -- "
                    f"pre-spot tenors (ON, TN) are not in the fallback set and use a different points "
                    f"convention, so this is never interpolated or extrapolated")
            else:
                detail = (
                    f"settle_date outside standard tenor range "
                    f"[{points[0].settle_date if points else '?'}, {points[-1].settle_date if points else '?'}], "
                    f"rejected (not extrapolated)")
            _fail(detail, CLASS_REJECTED)
            continue
        outright = outright_from_points(spot, interp_points, scale)
        out.append({
            "as_of_date": as_of.isoformat(), "instrument_id": r.instrument_id,
            "settle_date": r.settle_date, "mark_type": "FWD_OUTRIGHT", "value": outright,
            "source": SRC_INTERP, "snapped_at": snapped,
        })
        if diag is not None:
            diag.record_interpolation(pair, r.settle_date, points, scale, interp_points, outright, spot)
            diag.record_fwd_outright_result(r.instrument_id, r.settle_date, "interp",
                                             source=SRC_INTERP, value=outright)
    return out, warnings, failures


def _stage_timeout_failures(requests: Sequence[RequestRow], mark_type: str, e: "BloombergRequestError") -> List[dict]:
    """When an entire stage's batched request times out, every row requested for that
    mark_type failed -- one entry per row (not just one synthetic stage-level entry) so
    C-A's requested-vs-written reconciliation and summary.failures both reflect exactly
    which keys are missing and why."""
    return [
        {"instrument_id": r.instrument_id, "settle_date": r.settle_date, "mark_type": mark_type,
         "classification": e.classification, "detail": str(e)}
        for r in requests if r.mark_type == mark_type
    ]


def run(session, service, requests: Sequence[RequestRow], as_of: date,
        diag: Optional[Diagnostics] = None) -> Tuple[List[dict], List[str], List[dict]]:
    """Runs all three mark-type stages. Each stage is isolated: a BloombergRequestError
    (currently only raised on TIMEOUT) in one stage is caught and recorded in the
    returned `failures` list (both a synthetic stage-level entry and one entry per
    requested row of that stage/mark_type), and the remaining stages still run, so a
    stalled FWD_OUTRIGHT pull does not also discard SPOT/FUTURE_PX rows that already came
    back successfully. `failures` is not the sole signal of a partial run any more (see
    module docstring): callers must compare the full requested-key set against the
    written rows -- see run_pull(), which does this reconciliation and is the only place
    that decides whether the marks CSV is "partial"."""
    if diag is None:
        diag = Diagnostics()
    all_rows: List[dict] = []
    all_warnings: List[str] = []
    failures: List[dict] = []

    spot_batch_classification: Optional[str] = None
    spot_batch_detail: Optional[str] = None
    try:
        spot_rows, w, f = build_spot_rows(session, service, requests, as_of, diag)
        # W-2: captured here (immediately after the SPOT stage's own request), not later
        # inside build_fwd_outright_rows, since by then diag.requests has grown with
        # FUTURE_PX/direct-outright/tenor requests and the "most recent" entry would no
        # longer be the SPOT batch's.
        spot_batch_classification, spot_batch_detail = _batch_classification(diag)
    except BloombergRequestError as e:
        failures.append({"stage": "SPOT", "error": str(e)})
        f = _stage_timeout_failures(requests, "SPOT", e)
        spot_rows, w = [], [f"SPOT request failed: {e}"]
        spot_batch_classification, spot_batch_detail = e.classification, str(e)
    all_rows += spot_rows
    all_warnings += w
    failures += f
    spot_by_instrument = {r["instrument_id"]: r["value"] for r in spot_rows}

    try:
        future_rows, w, f = build_future_rows(session, service, requests, as_of, diag)
    except BloombergRequestError as e:
        failures.append({"stage": "FUTURE_PX", "error": str(e)})
        f = _stage_timeout_failures(requests, "FUTURE_PX", e)
        future_rows, w = [], [f"FUTURE_PX request failed: {e}"]
    all_rows += future_rows
    all_warnings += w
    failures += f

    try:
        fwd_rows, w, f = build_fwd_outright_rows(
            session, service, requests, as_of, spot_by_instrument, diag,
            spot_batch_classification=spot_batch_classification, spot_batch_detail=spot_batch_detail,
        )
    except BloombergRequestError as e:
        failures.append({"stage": "FWD_OUTRIGHT", "error": str(e)})
        f = _stage_timeout_failures(requests, "FWD_OUTRIGHT", e)
        fwd_rows, w = [], [f"FWD_OUTRIGHT request failed: {e}"]
    all_rows += fwd_rows
    all_warnings += w
    failures += f

    return all_rows, all_warnings, failures


def write_marks_csv(rows: Sequence[dict], path) -> None:
    # S-1: create the output directory if it doesn't exist yet -- without this, a missing
    # parent directory raised FileNotFoundError here, which main()'s except-Exception
    # handler turned into an exit 3 ("unhandled exception") that discarded rows already
    # successfully pulled from Bloomberg (they were only ever held in memory). Marks a
    # pull already paid the network round-trips for must never be lost to something this
    # trivial to avoid.
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=MARKS_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)


# --------------------------------------------------------------------------- probe mode
def run_probe(args, diag: Diagnostics) -> int:
    """Fixed exploratory sequence (see module docstring "--probe mode"). Never raises on
    an individual step's failure; every step is recorded in diag.requests, tagged with
    probe_name/probe_description/candidate/answers_question. Returns 0 as long as the
    session and //blp/refdata could be opened, regardless of individual step outcomes
    (those are exploratory data, not pass/fail for the probe run itself)."""
    as_of = date.fromisoformat(args.as_of)
    target_settle = (as_of + timedelta(days=30)).isoformat()

    try:
        session, service = open_session(args.host, args.port, diag=diag)
    except Exception as e:
        diag.record_synthetic("SESSION", CLASS_SESSION_ERROR, str(e),
                               {"probe_name": "session_start",
                                "probe_description": "Start blpapi session and open //blp/refdata"})
        diag.run_info = {"as_of": args.as_of, "mode": "probe"}
        diag.finalize_summary(exit_code=4, failures=[{"stage": "probe_session", "error": str(e)}],
                               marks_csv_partial=False, mode="probe", outcome="PROBE_SESSION_FAILED")
        print(f"ERROR: {e}", file=sys.stderr)
        return 4

    diag.record_synthetic("SESSION", CLASS_OK, None,
                           {"probe_name": "session_start",
                            "probe_description": "Start blpapi session and open //blp/refdata"})

    def step(name, description, fn, answers_question=None, candidate=False):
        tag = {"probe_name": name, "probe_description": description, "candidate": candidate}
        if answers_question is not None:
            tag["answers_question"] = answers_question
        try:
            result = fn(tag)
        except BloombergRequestError:
            return  # already recorded by fetch_reference/fetch_historical before raising
        except Exception as e:
            diag.record_synthetic("PROBE_STEP", CLASS_EXCEPTION, f"{type(e).__name__}: {e}", tag)
            return
        # W-4/S-3: run_probe calls fetch_reference/fetch_historical directly, bypassing
        # fetch_fwd_outright_direct()'s scalar check -- record the returned value(s) as
        # evidence and flag explicitly when a candidate field came back non-scalar (a
        # bulk field), which would otherwise silently read as "OK: None". Checked for
        # every value of a candidate step (not just FWD_CURVE by name) so the
        # FWD_OUTRIGHT_PRICE candidate (fwd_outright_direct_alt_fwd_outright_field) gets
        # the same protection as the FWD_CURVE candidates -- either could equally come
        # back as a bulk list if Bloomberg doesn't pin it down to one row via SETTLE_DT.
        # Restricted to candidate steps (plus FWD_CURVE/FWD_OUTRIGHT_PRICE by name as a
        # belt-and-suspenders for any future non-candidate use of those fields) so a
        # legitimately non-numeric scalar field on a non-candidate step -- e.g. SETTLE_DT
        # (a date string) on tenor_1m/tenor_3m -- isn't misflagged as "non-scalar/bulk".
        rec = diag.requests[-1] if diag.requests and diag.requests[-1].get("probe_name") == name else None
        if rec is not None and rec.get("classification") == CLASS_OK:
            scalar = True
            if isinstance(result, dict):
                for row in result.values():
                    for fname, v in (row.items() if isinstance(row, dict) else []):
                        if candidate or fname in ("FWD_CURVE", "FWD_OUTRIGHT_PRICE"):
                            if isinstance(v, bool) or not isinstance(v, (int, float)):
                                scalar = False
            rec["scalar"] = scalar
            rec["detail"] = ("OK: non-scalar value(s): " if not scalar else "OK: ") + repr(result)

    step("spot_reference", "PX_LAST on EURUSD Curncy via ReferenceDataRequest",
         lambda tag: fetch_reference(session, service, ["EURUSD Curncy"], ["PX_LAST"], diag=diag, tag=tag),
         answers_question=29)

    step("spot_historical", "PX_LAST on EURUSD Curncy via HistoricalDataRequest for --as-of",
         lambda tag: fetch_historical(session, service, ["EURUSD Curncy"], "PX_LAST", as_of, diag=diag, tag=tag),
         answers_question=29)

    step("fwd_outright_direct_primary",
         f"Direct broken-date FWD_OUTRIGHT for EURUSD, settle={target_settle}: ReferenceDataRequest field "
         f"FWD_CURVE, overrides FWD_CURVE_QUOTE_FORMAT=OUTRIGHTS, SETTLE_DT",
         lambda tag: fetch_reference(session, service, ["EURUSD Curncy"], ["FWD_CURVE"],
                                      overrides={"FWD_CURVE_QUOTE_FORMAT": "OUTRIGHTS",
                                                 "SETTLE_DT": target_settle.replace("-", "")},
                                      diag=diag, tag=tag),
         answers_question=27, candidate=True)

    step("fwd_outright_direct_alt_reference_date",
         f"Alternative candidate: FWD_CURVE with an added REFERENCE_DATE override (as_of), "
         f"settle={target_settle}",
         lambda tag: fetch_reference(session, service, ["EURUSD Curncy"], ["FWD_CURVE"],
                                      overrides={"FWD_CURVE_QUOTE_FORMAT": "OUTRIGHTS",
                                                 "REFERENCE_DATE": as_of.strftime("%Y%m%d"),
                                                 "SETTLE_DT": target_settle.replace("-", "")},
                                      diag=diag, tag=tag),
         answers_question=27, candidate=True)

    step("fwd_outright_direct_alt_fwd_outright_field",
         f"Alternative candidate: FX_FWD_OUTRIGHT-style field (FWD_OUTRIGHT_PRICE) with SETTLE_DT override, "
         f"settle={target_settle}",
         lambda tag: fetch_reference(session, service, ["EURUSD Curncy"], ["FWD_OUTRIGHT_PRICE"],
                                      overrides={"SETTLE_DT": target_settle.replace("-", "")},
                                      diag=diag, tag=tag),
         answers_question=27, candidate=True)

    step("tenor_1m", "EURUSD1M Curncy PX_LAST + SETTLE_DT",
         lambda tag: fetch_reference(session, service, ["EURUSD1M Curncy"], ["PX_LAST", "SETTLE_DT"],
                                      diag=diag, tag=tag),
         answers_question=28)

    step("tenor_3m", "EURUSD3M Curncy PX_LAST + SETTLE_DT",
         lambda tag: fetch_reference(session, service, ["EURUSD3M Curncy"], ["PX_LAST", "SETTLE_DT"],
                                      diag=diag, tag=tag),
         answers_question=28)

    step("fwd_points_scale", "FWD_POINTS_SCALE on EURUSD Curncy",
         lambda tag: fetch_reference(session, service, ["EURUSD Curncy"], ["FWD_POINTS_SCALE"],
                                      diag=diag, tag=tag),
         answers_question=28)

    step("es_settle_px_settle", "PX_SETTLE on ESU6 Index via HistoricalDataRequest for --as-of",
         lambda tag: fetch_historical(session, service, ["ESU6 Index"], "PX_SETTLE", as_of, diag=diag, tag=tag),
         answers_question=31)

    step("es_settle_px_last", "PX_LAST on ESU6 Index via HistoricalDataRequest for --as-of",
         lambda tag: fetch_historical(session, service, ["ESU6 Index"], "PX_LAST", as_of, diag=diag, tag=tag),
         answers_question=31)

    session.stop()

    probe_requests = [r for r in diag.requests if r.get("probe_name")]
    failures = [
        {"stage": r["probe_name"], "error": r.get("detail"), "classification": r.get("classification"),
         "candidate": bool(r.get("candidate"))}
        for r in probe_requests if r.get("classification") in _FAILURE_CLASSIFICATIONS
    ]
    # C-B: candidate steps (the fwd_outright_direct_* alternatives) are exploratory --
    # trying several field/override guesses is expected to leave some failing, so those
    # don't make the probe run itself look failed. A non-candidate step failing (spot,
    # tenor, ES settle probes -- things we're fairly confident about the field names for)
    # does, and outcome must say so explicitly rather than default to "OK" just because
    # exit_code is 0 (probe mode always exits 0 once the session opens).
    non_candidate_failures = [f for f in failures if not f["candidate"]]
    outcome = "PROBE_COMPLETE_WITH_FAILURES" if non_candidate_failures else "OK"
    diag.run_info = {"as_of": args.as_of, "mode": "probe", "target_settle_date": target_settle}
    diag.finalize_summary(exit_code=0, failures=failures, marks_csv_partial=False, mode="probe", outcome=outcome)
    print(f"probe complete: {len(probe_requests)} step(s) recorded "
          f"({len(failures)} reported a non-OK classification, {len(non_candidate_failures)} of those "
          f"non-candidate -- see the diag JSON; candidate failures are expected exploratory output)",
          file=sys.stderr)
    return 0


# --------------------------------------------------------------------------- normal pull
def run_pull(args, diag: Diagnostics) -> int:
    as_of = date.fromisoformat(args.as_of)
    requests = read_request_csv(args.request)

    try:
        check_not_stale(as_of, requests, args.allow_stale_as_of)
    except StaleAsOfError as e:
        diag.run_info = {"as_of": args.as_of, "request_csv": args.request, "out": args.out}
        diag.finalize_summary(exit_code=2, failures=[{"stage": "stale_check", "error": str(e)}],
                               marks_csv_partial=False, requested_rows=len(requests), written_rows=0)
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    try:
        session, service = open_session(args.host, args.port, diag=diag)
    except Exception as e:
        diag.run_info = {"as_of": args.as_of, "request_csv": args.request, "out": args.out}
        diag.finalize_summary(exit_code=4, failures=[{"stage": "session_open", "error": str(e)}],
                               marks_csv_partial=False, requested_rows=len(requests), written_rows=0)
        print(f"ERROR: {e}", file=sys.stderr)
        return 4

    try:
        rows, warnings, failures = run(session, service, requests, as_of, diag)
    finally:
        session.stop()

    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)

    # Always write whatever rows were obtained -- useful for review even when partial --
    # but the exit code and the diagnostics summary make it unambiguous that this is not
    # a complete, trustworthy marks CSV (reviewer finding: never exit 0 with a partial CSV).
    write_marks_csv(rows, args.out)

    # C-A: "partial" is defined by comparing the requested key set against what was
    # actually written -- NOT by whether `failures` happens to be non-empty. This is the
    # only correct definition: a request that fails silently for a reason we didn't
    # anticipate (any of build_spot_rows/build_future_rows/build_fwd_outright_rows'
    # per-row skip paths) must still make the run fail, even if some earlier bookkeeping
    # missed recording a failures entry for it.
    requested_keys = {(r.instrument_id, r.settle_date, r.mark_type) for r in requests}
    written_keys = {(r["instrument_id"], r["settle_date"], r["mark_type"]) for r in rows}
    missing_keys = requested_keys - written_keys
    existing_failure_keys = {(f.get("instrument_id"), f.get("settle_date"), f.get("mark_type")) for f in failures}
    for iid, sd, mt in missing_keys - existing_failure_keys:
        failures.append({
            "instrument_id": iid, "settle_date": sd, "mark_type": mt,
            "classification": CLASS_NO_VALUE,
            "detail": "row missing from output; no specific failure was recorded for it upstream",
        })
    partial = bool(missing_keys)
    exit_code = 0 if not partial else 5
    diag.run_info = {
        "as_of": args.as_of, "request_csv": args.request, "out": args.out,
        "requested_rows": len(requests), "written_rows": len(rows), "warnings": warnings,
    }
    diag.finalize_summary(exit_code=exit_code, failures=failures, marks_csv_partial=partial,
                           requested_rows=len(requests), written_rows=len(rows))

    if partial:
        print(f"ERROR: {len(missing_keys)} requested key(s) were never written: {sorted(missing_keys)}; "
              f"marks CSV at {args.out} is PARTIAL ({len(rows)} row(s) written -- not all requested marks "
              f"were obtained)", file=sys.stderr)
    print(f"wrote {len(rows)} row(s) to {args.out} ({len(warnings)} warning(s))", file=sys.stderr)
    return exit_code


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--request", help="request CSV path (required unless --probe)")
    parser.add_argument("--as-of", required=True, help="as_of date, ISO YYYY-MM-DD")
    parser.add_argument("--out", required=True,
                         help="output marks CSV path (probe mode: no CSV is written, but this still names "
                              "the diagnostics file, <out>.diag.json)")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8194)
    parser.add_argument("--allow-stale-as-of", action="store_true",
                         help="allow FWD_OUTRIGHT rows when --as-of is not today (America/New_York); "
                              "without this flag the run is refused (see module docstring)")
    parser.add_argument("--probe", action="store_true",
                         help="run a fixed diagnostic probe sequence instead of a normal pull; "
                              "no --request needed, no marks CSV written, only <out>.diag.json")
    args = parser.parse_args(argv)

    if not args.probe and not args.request:
        parser.error("--request is required unless --probe is given")

    diag = Diagnostics()
    dpath = diag_path_for(args.out)

    # tz prerequisite check: resolve _ny() here, immediately after Diagnostics() is
    # created and before the first record_environment() call (which itself calls _ny()
    # for machine_time_america_new_york), so a machine missing the `tzdata` pip package
    # (stock Windows Python has no IANA tz database) gets a diag JSON + stderr hint +
    # exit 6 instead of crashing with nothing written (docs/open-questions.md item 39).
    try:
        _ny()
    except ZoneInfoNotFoundError as e:
        diag.run_info = {"as_of": args.as_of, "out": args.out, "mode": "probe" if args.probe else "pull"}
        diag.finalize_summary(
            exit_code=6, failures=[{"stage": "tz_prerequisite",
                                     "error": f"America/New_York zoneinfo not available: {e}",
                                     "hint": "py -3 -m pip install tzdata"}],
            marks_csv_partial=False, mode="probe" if args.probe else "pull",
        )
        write_diagnostics(diag, dpath)
        print("ERROR: America/New_York zoneinfo not available (tzdata missing); "
              "hint: py -3 -m pip install tzdata", file=sys.stderr)
        return 6

    # W-2: record a baseline environment block immediately, before anything that could
    # fail (read_request_csv, check_not_stale, open_session) -- so a diag written on a
    # stale-as-of refusal, a missing request CSV, or any other early failure still has a
    # non-empty environment block. open_session() (called from run_pull/run_probe) later
    # overwrites this with session_started/service_opened once it knows them.
    diag.record_environment(args.host, args.port)

    # S-2: validate --as-of is a real ISO date here, before run_pull()/run_probe() (both
    # of which call date.fromisoformat(args.as_of) themselves). A malformed date is an
    # argument error -- exit 2, the same code used below for a stale --as-of refusal --
    # not the generic unhandled-exception path (exit 3) that a bare ValueError would
    # otherwise fall into several calls deep. The diag JSON is still written (with this
    # failure recorded) so a bad invocation leaves the same audit trail as every other
    # failure mode.
    try:
        date.fromisoformat(args.as_of)
    except ValueError as e:
        diag.run_info = {"as_of": args.as_of, "out": args.out, "mode": "probe" if args.probe else "pull"}
        diag.finalize_summary(
            exit_code=2, failures=[{"stage": "as_of_validation",
                                     "error": f"--as-of {args.as_of!r} is not a valid ISO date: {e}"}],
            marks_csv_partial=False, mode="probe" if args.probe else "pull",
        )
        write_diagnostics(diag, dpath)
        print(f"ERROR: --as-of {args.as_of!r} is not a valid ISO date (YYYY-MM-DD): {e}", file=sys.stderr)
        return 2

    exit_code = 1
    try:
        if args.probe:
            exit_code = run_probe(args, diag)
        else:
            exit_code = run_pull(args, diag)
    except Exception as e:
        diag.record_exception(e)
        if not diag.run_info:
            diag.run_info = {"as_of": args.as_of, "out": args.out, "mode": "probe" if args.probe else "pull"}
        diag.finalize_summary(
            exit_code=3, failures=[{"stage": "unhandled", "error": f"{type(e).__name__}: {e}"}],
            marks_csv_partial=not args.probe, mode="probe" if args.probe else "pull",
        )
        print(f"ERROR: unhandled exception: {type(e).__name__}: {e}", file=sys.stderr)
        exit_code = 3
    finally:
        write_diagnostics(diag, dpath)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
