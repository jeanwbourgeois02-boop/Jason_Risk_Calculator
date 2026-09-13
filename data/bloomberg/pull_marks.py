#!/usr/bin/env python
"""Standalone Bloomberg-machine script: pull marks and write the canonical marks CSV.

Runs on a machine with `blpapi` and network access to a running Bloomberg Terminal /
B-PIPE (`//blp/refdata` service on localhost:8194 by default). Deliberately dependency-
light (blpapi, stdlib only) and self-contained: it does NOT import anything from this
repository (data.bloomberg.marks_csv etc.), so it can be copied as a single file to the
Bloomberg machine, which has no access to this repo.

CLI:
    py -3 pull_marks.py --request req.csv --as-of 2026-08-17 --out marks.csv
    py -3 pull_marks.py --request req.csv --as-of 2026-08-17 --out marks.csv \
        --host localhost --port 8194
    py -3 pull_marks.py --request req.csv --as-of 2026-08-17 --out marks.csv \
        --allow-stale-as-of   # only needed when --as-of != today in America/New_York

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
    to tenor interpolation, logged as a warning), never passed to float().
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
"""
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

# Mirrors data.bloomberg.marks_csv.MARKS_COLUMNS. Duplicated (not imported) because this
# script must be copyable as a single file with no repo imports.
MARKS_COLUMNS = ["as_of_date", "instrument_id", "settle_date", "mark_type", "value", "source", "snapped_at"]

REQUEST_COLUMNS = ["instrument_id", "bbg_ticker", "settle_date", "mark_type"]

SRC_SPOT_FWD = "BBG_BFXFORWARD"
SRC_FUTURE = "BBG_BDH"
SRC_INTERP = "BBG_INTERP"

NY = ZoneInfo("America/New_York")

# ON/TN deliberately excluded: their settle dates are before spot (SP) and their points
# use the pre-spot quoting convention (e.g. outright = spot - points for TN), which
# outright_from_points()'s spot + points/scale would apply incorrectly. See module
# docstring "FWD_OUTRIGHT fallback".
STANDARD_TENORS = ["SP", "1W", "2W", "1M", "2M", "3M", "6M", "1Y"]

# nextEvent() timeout so a stalled/unreachable session doesn't hang forever.
EVENT_TIMEOUT_MS = 30000


def snapped_at(as_of: date) -> str:
    """15:00 America/New_York on as_of, ISO with the resolved offset for that date."""
    return datetime(as_of.year, as_of.month, as_of.day, 15, 0, 0, tzinfo=NY).isoformat()


class StaleAsOfError(RuntimeError):
    """Raised when --as-of is not today (America/New_York) and the request includes
    FWD_OUTRIGHT rows, without --allow-stale-as-of. See module docstring
    "Staleness guard"."""


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
    today = today if today is not None else datetime.now(NY).date()
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


# --------------------------------------------------------------------------- network layer
def _get_blpapi():
    """Import blpapi lazily so a fake module injected into sys.modules['blpapi'] before
    this is called (e.g. by a test) is picked up, and so importing this module for its
    pure functions (interpolation, CSV assembly) never requires blpapi to be installed.
    """
    import blpapi  # noqa: F401 (imported for side effect / availability check)
    return blpapi


def open_session(host: str = "localhost", port: int = 8194):
    """Open a blpapi Session and start the //blp/refdata service. Returns (session, service)."""
    blpapi = _get_blpapi()
    opts = blpapi.SessionOptions()
    opts.setServerHost(host)
    opts.setServerPort(port)
    session = blpapi.Session(opts)
    if not session.start():
        raise RuntimeError(f"failed to start blpapi session at {host}:{port}")
    if not session.openService("//blp/refdata"):
        raise RuntimeError("failed to open //blp/refdata")
    service = session.getService("//blp/refdata")
    return session, service


def fetch_reference(session, service, tickers: Sequence[str], fields: Sequence[str],
                     overrides: Optional[Dict[str, str]] = None) -> Dict[str, Dict[str, object]]:
    """Thin network layer: ReferenceDataRequest for `tickers` x `fields`, with optional
    field overrides (e.g. SETTLE_DT). Returns {ticker: {field: value}}, plain dicts only
    (no blpapi objects escape this function), so callers and tests never touch blpapi
    types directly.

    A field missing from the response (blpapi field-not-applicable / error) is simply
    absent from that ticker's dict rather than raising -- callers decide whether that
    means "fall back" or "reject".
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

    session.sendRequest(request)
    blpapi = _get_blpapi()
    out: Dict[str, Dict[str, object]] = {}
    while True:
        event = session.nextEvent(EVENT_TIMEOUT_MS)
        event_type = event.eventType()
        if event_type == getattr(blpapi.Event, "TIMEOUT", None):
            print(f"WARNING: nextEvent timed out after {EVENT_TIMEOUT_MS}ms waiting for "
                  f"ReferenceDataRequest response", file=sys.stderr)
            break
        for msg in event:
            if not msg.hasElement("securityData"):
                continue
            sec_data = msg.getElement("securityData")
            for i in range(sec_data.numValues()):
                sd = sec_data.getValueAsElement(i)
                ticker = sd.getElementAsString("security")
                row: Dict[str, object] = {}
                if sd.hasElement("fieldData"):
                    fd = sd.getElement("fieldData")
                    for f in fields:
                        if fd.hasElement(f):
                            row[f] = fd.getElement(f).getValue()
                out[ticker] = row
        if event_type == blpapi.Event.RESPONSE:
            break
    return out


def fetch_historical(session, service, tickers: Sequence[str], field: str,
                      as_of: date) -> Dict[str, Optional[float]]:
    """Thin network layer: HistoricalDataRequest for a single date (start = end = as_of).
    Returns {ticker: value or None}.
    """
    request = service.createRequest("HistoricalDataRequest")
    for t in tickers:
        request.getElement("securities").appendValue(t)
    request.getElement("fields").appendValue(field)
    d = as_of.strftime("%Y%m%d")
    request.set("startDate", d)
    request.set("endDate", d)

    session.sendRequest(request)
    blpapi = _get_blpapi()
    out: Dict[str, Optional[float]] = {}
    while True:
        event = session.nextEvent(EVENT_TIMEOUT_MS)
        event_type = event.eventType()
        if event_type == getattr(blpapi.Event, "TIMEOUT", None):
            print(f"WARNING: nextEvent timed out after {EVENT_TIMEOUT_MS}ms waiting for "
                  f"HistoricalDataRequest response", file=sys.stderr)
            break
        for msg in event:
            if not msg.hasElement("securityData"):
                continue
            sec_data = msg.getElement("securityData")
            ticker = sec_data.getElementAsString("security")
            value = None
            if sec_data.hasElement("fieldData"):
                fd = sec_data.getElement("fieldData")
                if fd.numValues() > 0:
                    point = fd.getValueAsElement(0)
                    if point.hasElement(field):
                        value = point.getElement(field).getValue()
            out[ticker] = value
        if event_type == blpapi.Event.RESPONSE:
            break
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
def build_spot_rows(session, service, requests: Sequence[RequestRow], as_of: date) -> Tuple[List[dict], List[str]]:
    """SPOT: HistoricalDataRequest PX_LAST for as_of (consistent with FUTURE_PX -- both
    read the as_of close, not a live quote, so snapped_at is never mislabeled regardless
    of when the script actually runs). See module docstring."""
    rows = [r for r in requests if r.mark_type == "SPOT"]
    if not rows:
        return [], []
    tickers = sorted({r.bbg_ticker for r in rows})
    data = fetch_historical(session, service, tickers, "PX_LAST", as_of)
    out, warnings = [], []
    snapped = snapped_at(as_of)
    for r in rows:
        val = data.get(r.bbg_ticker)
        if val is None:
            warnings.append(f"SPOT {r.instrument_id} ({r.bbg_ticker}): no PX_LAST returned for {as_of}")
            continue
        out.append({
            "as_of_date": as_of.isoformat(), "instrument_id": r.instrument_id,
            "settle_date": r.settle_date, "mark_type": "SPOT", "value": float(val),
            "source": SRC_SPOT_FWD, "snapped_at": snapped,
        })
    return out, warnings


def build_future_rows(session, service, requests: Sequence[RequestRow], as_of: date) -> Tuple[List[dict], List[str]]:
    """FUTURE_PX: HistoricalDataRequest PX_SETTLE for as_of. See module docstring."""
    rows = [r for r in requests if r.mark_type == "FUTURE_PX"]
    if not rows:
        return [], []
    tickers = sorted({r.bbg_ticker for r in rows})
    data = fetch_historical(session, service, tickers, "PX_SETTLE", as_of)
    out, warnings = [], []
    snapped = snapped_at(as_of)
    for r in rows:
        val = data.get(r.bbg_ticker)
        if val is None:
            warnings.append(f"FUTURE_PX {r.instrument_id} ({r.bbg_ticker}): no PX_SETTLE returned for {as_of}")
            continue
        out.append({
            "as_of_date": as_of.isoformat(), "instrument_id": r.instrument_id,
            "settle_date": r.settle_date, "mark_type": "FUTURE_PX", "value": float(val),
            "source": SRC_FUTURE, "snapped_at": snapped,
        })
    return out, warnings


def fetch_fwd_outright_direct(session, service, bbg_ticker: str, settle_date: str,
                               warnings: Optional[List[str]] = None) -> Optional[float]:
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


def fetch_tenor_points(session, service, pair: str) -> Tuple[List[TenorPoint], Optional[float]]:
    """Fetch standard-tenor forward points (PX_LAST) and settlement dates (SETTLE_DT)
    for '<pair><TENOR> Curncy', plus the pair's FWD_POINTS_SCALE from '<pair> Curncy'.
    Tenors whose response is missing either field are skipped, not defaulted.
    """
    tenor_tickers = {tenor: f"{pair}{tenor} Curncy" for tenor in STANDARD_TENORS}
    data = fetch_reference(session, service, list(tenor_tickers.values()), ["PX_LAST", "SETTLE_DT"])
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

    scale_data = fetch_reference(session, service, [f"{pair} Curncy"], ["FWD_POINTS_SCALE"])
    scale_val = scale_data.get(f"{pair} Curncy", {}).get("FWD_POINTS_SCALE")
    scale = float(scale_val) if scale_val is not None else None
    return points, scale


def build_fwd_outright_rows(session, service, requests: Sequence[RequestRow], as_of: date,
                             spot_rows_by_instrument: Dict[str, float]) -> Tuple[List[dict], List[str]]:
    """FWD_OUTRIGHT: try the direct broken-date request first; fall back to standard-
    tenor forward-point interpolation. See module docstring for field/override names."""
    rows = [r for r in requests if r.mark_type == "FWD_OUTRIGHT"]
    if not rows:
        return [], []
    out, warnings = [], []
    snapped = snapped_at(as_of)
    tenor_cache: Dict[str, Tuple[List[TenorPoint], Optional[float]]] = {}

    for r in rows:
        pair = r.instrument_id
        target = date.fromisoformat(r.settle_date)

        direct = fetch_fwd_outright_direct(session, service, r.bbg_ticker, r.settle_date, warnings)
        if direct is not None:
            out.append({
                "as_of_date": as_of.isoformat(), "instrument_id": r.instrument_id,
                "settle_date": r.settle_date, "mark_type": "FWD_OUTRIGHT", "value": direct,
                "source": SRC_SPOT_FWD, "snapped_at": snapped,
            })
            continue

        # fallback: tenor interpolation
        if pair not in tenor_cache:
            tenor_cache[pair] = fetch_tenor_points(session, service, pair)
        points, scale = tenor_cache[pair]
        spot = spot_rows_by_instrument.get(pair)
        if spot is None:
            warnings.append(f"FWD_OUTRIGHT {pair} {r.settle_date}: no direct value and no SPOT to base interpolation on")
            continue
        if scale is None:
            warnings.append(f"FWD_OUTRIGHT {pair} {r.settle_date}: no FWD_POINTS_SCALE returned, cannot interpolate")
            continue
        interp_points = interpolate_forward_points(target, points)
        if interp_points is None:
            if points and target < points[0].settle_date:
                warnings.append(
                    f"FWD_OUTRIGHT {pair} {r.settle_date}: settle_date is before spot "
                    f"({points[0].settle_date.isoformat()}), rejected -- pre-spot tenors (ON, TN) "
                    f"are not in the fallback set and use a different points convention, so this "
                    f"is never interpolated or extrapolated")
            else:
                warnings.append(
                    f"FWD_OUTRIGHT {pair} {r.settle_date}: settle_date outside standard tenor range "
                    f"[{points[0].settle_date if points else '?'}, {points[-1].settle_date if points else '?'}], "
                    f"rejected (not extrapolated)")
            continue
        outright = outright_from_points(spot, interp_points, scale)
        out.append({
            "as_of_date": as_of.isoformat(), "instrument_id": r.instrument_id,
            "settle_date": r.settle_date, "mark_type": "FWD_OUTRIGHT", "value": outright,
            "source": SRC_INTERP, "snapped_at": snapped,
        })
    return out, warnings


def run(session, service, requests: Sequence[RequestRow], as_of: date) -> Tuple[List[dict], List[str]]:
    all_rows: List[dict] = []
    all_warnings: List[str] = []

    spot_rows, w = build_spot_rows(session, service, requests, as_of)
    all_rows += spot_rows
    all_warnings += w
    spot_by_instrument = {r["instrument_id"]: r["value"] for r in spot_rows}

    future_rows, w = build_future_rows(session, service, requests, as_of)
    all_rows += future_rows
    all_warnings += w

    fwd_rows, w = build_fwd_outright_rows(session, service, requests, as_of, spot_by_instrument)
    all_rows += fwd_rows
    all_warnings += w

    return all_rows, all_warnings


def write_marks_csv(rows: Sequence[dict], path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=MARKS_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, help="request CSV path")
    parser.add_argument("--as-of", required=True, help="as_of date, ISO YYYY-MM-DD")
    parser.add_argument("--out", required=True, help="output marks CSV path")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8194)
    parser.add_argument("--allow-stale-as-of", action="store_true",
                         help="allow FWD_OUTRIGHT rows when --as-of is not today (America/New_York); "
                              "without this flag the run is refused (see module docstring)")
    args = parser.parse_args(argv)

    as_of = date.fromisoformat(args.as_of)
    requests = read_request_csv(args.request)
    try:
        check_not_stale(as_of, requests, args.allow_stale_as_of)
    except StaleAsOfError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    session, service = open_session(args.host, args.port)
    try:
        rows, warnings = run(session, service, requests, as_of)
    finally:
        session.stop()

    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)

    write_marks_csv(rows, args.out)
    print(f"wrote {len(rows)} row(s) to {args.out} ({len(warnings)} warning(s))", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
