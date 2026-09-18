"""Forward outrights from Bloomberg's bulk FWD_CURVE field.

On a real terminal `FWD_CURVE` (with override FWD_CURVE_QUOTE_FORMAT=OUTRIGHTS) is a
TABLE, one row per standard tenor, not a scalar -- which is why the earlier direct
request reported "FWD_CURVE not a scalar". This module requests that table once per
pair, parses it into (settle_date, outright) points, and derives the outright for any
requested settle date:
  * exact tenor date -> that row's value;
  * between two tenors -> linear interpolation in outright space (flagged INTERP);
  * between spot date and the first tenor -> interpolation from the live spot;
  * beyond the last tenor -> no value (never extrapolated).
Nothing is invented: a pair whose table cannot be parsed is reported with the raw
column names so the field mapping can be corrected.

`request_fwd_curves` talks to blpapi directly (element access is needed for bulk data);
`points_from_rows` / `outright_for_date` are pure and unit-tested with plain dicts.
tools/bloomberg_diagnostic.py carries its own copy of this logic (standalone file).

`historical_points_by_day` (2026-09-18, backfill) does the same "rows -> (settle_date,
outright) points" job for a PAST day: `FWD_CURVE` itself is a bulk/table field, which
Bloomberg does not serve through `HistoricalDataRequest` the way it does through
`ReferenceDataRequest` (see data/bloomberg/pull_marks.py's own module docstring, which
already documents this as a known limitation of the live pull's tenor-fallback path) --
so a day's curve is assembled instead from the STANDARD-TENOR outright tickers
(`data.bloomberg.pull_marks.STANDARD_TENORS`, e.g. 'EURUSD1M Curncy'), each queried
historically for both PX_LAST and SETTLE_DT via
`data.bloomberg.pull_marks.fetch_historical_series`. UNVERIFIED: that a rolling-tenor
ticker's SETTLE_DT is available (and correct for that day) via HistoricalDataRequest the
same way it is live via ReferenceDataRequest -- see docs/bloomberg-pc-checklist.md.
`outright_for_date` (below) is then reused unchanged for the actual interpolation, exactly
as the live path uses it.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

Point = Tuple[date, float]

_MID_KEYS = ("MID", "OUTRIGHT", "RATE", "PX_MID", "VALUE")
_DATE_KEYS = ("SETTLE", "DATE", "MATURITY")


def _to_date(v) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        for fmt in ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y"):
            try:
                return datetime.strptime(v[:10], fmt).date()
            except ValueError:
                continue
    return None


def points_from_rows(rows: List[dict]) -> Tuple[List[Point], List[str]]:
    """rows: list of {column_name: value} for each tenor. Returns (sorted points, columns).
    Date column: first column whose name contains SETTLE/DATE/MATURITY with a parseable
    date. Value: column named like MID/OUTRIGHT/RATE; else mean of BID and ASK; else the
    single numeric column. Rows without both are skipped."""
    points: List[Point] = []
    columns: List[str] = []
    for row in rows:
        if not columns:
            columns = list(row.keys())
        settle = None
        for k, v in row.items():
            if any(t in k.upper() for t in _DATE_KEYS):
                settle = _to_date(v)
                if settle:
                    break
        if settle is None:
            for v in row.values():
                settle = _to_date(v)
                if settle:
                    break
        numeric = {k: float(v) for k, v in row.items()
                   if isinstance(v, (int, float)) and not isinstance(v, bool)}
        value = None
        for k, v in numeric.items():
            if any(t in k.upper() for t in _MID_KEYS):
                value = v
                break
        if value is None:
            bid = next((v for k, v in numeric.items() if "BID" in k.upper()), None)
            ask = next((v for k, v in numeric.items() if "ASK" in k.upper()), None)
            if bid is not None and ask is not None:
                value = (bid + ask) / 2.0
        if value is None and len(numeric) == 1:
            value = next(iter(numeric.values()))
        if settle is not None and value is not None and value > 0:
            points.append((settle, value))
    points.sort()
    return points, columns


def outright_for_date(points: List[Point], target: date, spot: Optional[float] = None,
                      spot_date: Optional[date] = None) -> Tuple[Optional[float], str]:
    """(outright, how) where how in {'EXACT', 'INTERP', 'INTERP_FROM_SPOT', ''}."""
    for d, v in points:
        if d == target:
            return v, "EXACT"
    before = [(d, v) for d, v in points if d < target]
    after = [(d, v) for d, v in points if d > target]
    if before and after:
        d0, v0 = before[-1]
        d1, v1 = after[0]
        w = (target - d0).days / (d1 - d0).days
        return v0 + w * (v1 - v0), "INTERP"
    if not before and after and spot is not None and spot_date is not None and spot_date < target:
        d1, v1 = after[0]
        w = (target - spot_date).days / (d1 - spot_date).days
        return spot + w * (v1 - spot), "INTERP_FROM_SPOT"
    return None, ""


def element_rows(bulk_element) -> List[dict]:
    """blpapi bulk Element (array of sequences) -> list of {name: python value}."""
    rows = []
    for i in range(bulk_element.numValues()):
        row_el = bulk_element.getValueAsElement(i)
        row = {}
        for j in range(row_el.numElements()):
            sub = row_el.getElement(j)
            name = str(sub.name())
            try:
                row[name] = sub.getValue() if sub.numValues() == 1 else str(sub)
            except Exception:
                row[name] = str(sub)
        rows.append(row)
    return rows


def request_fwd_curves(blpapi, session, service, tickers: List[str], timeout_ms: int = 15000
                       ) -> Dict[str, dict]:
    """{ticker: {'points': [...], 'columns': [...], 'error': str}} for FWD_CURVE in
    OUTRIGHTS format. Plain Python out; no blpapi objects escape.

    Sent with its own CorrelationId (2026-09-18): without one, a message left over from a
    PREVIOUS request on the same session/service that timed out (e.g. live.py's own SPOT
    ReferenceDataRequest a moment earlier in the same pull cycle) can arrive late and be
    read here as if it were this request's response -- its `securityData` (or lack of one)
    would silently produce an empty/wrong curve for every ticker rather than the real
    FWD_CURVE data, since nothing was checking whose response was whose. A message whose
    correlationIds() don't include this request's id is discarded (matches
    pull_marks.fetch_reference / fetch_historical's existing pattern)."""
    req = service.createRequest("ReferenceDataRequest")
    for t in tickers:
        req.getElement("securities").appendValue(t)
    req.getElement("fields").appendValue("FWD_CURVE")
    ov = req.getElement("overrides").appendElement()
    ov.setElement("fieldId", "FWD_CURVE_QUOTE_FORMAT")
    ov.setElement("value", "OUTRIGHTS")
    correlation_id = blpapi.CorrelationId(id(req))
    session.sendRequest(req, correlationId=correlation_id)
    out: Dict[str, dict] = {t: {"points": [], "columns": [], "error": "no response for ticker"} for t in tickers}
    while True:
        ev = session.nextEvent(timeout_ms)
        if ev.eventType() == blpapi.Event.TIMEOUT:
            for t in tickers:
                if out[t]["error"] == "no response for ticker":
                    out[t]["error"] = "TIMEOUT"
            break
        event_is_ours = False
        for msg in ev:
            msg_cids = list(msg.correlationIds()) if hasattr(msg, "correlationIds") else []
            if msg_cids and correlation_id not in msg_cids:
                continue  # late reply to a previous (e.g. timed-out) request, not ours
            event_is_ours = True
            if not msg.hasElement("securityData"):
                if msg.hasElement("responseError"):
                    for t in tickers:
                        out[t]["error"] = f"responseError: {msg.getElement('responseError')}"
                continue
            sec = msg.getElement("securityData")
            for i in range(sec.numValues()):
                sd = sec.getValueAsElement(i)
                t = sd.getElementAsString("security")
                if sd.hasElement("securityError"):
                    out[t] = {"points": [], "columns": [],
                              "error": "securityError: " + sd.getElement("securityError").getElementAsString("message")}
                    continue
                fe = []
                if sd.hasElement("fieldExceptions"):
                    fx = sd.getElement("fieldExceptions")
                    for k in range(fx.numValues()):
                        x = fx.getValueAsElement(k)
                        fe.append(f"{x.getElementAsString('fieldId')}: {x.getElement('errorInfo').getElementAsString('message')}")
                fd = sd.getElement("fieldData")
                if not fd.hasElement("FWD_CURVE"):
                    out[t] = {"points": [], "columns": [], "error": "fieldExceptions: " + "; ".join(fe) if fe else "FWD_CURVE absent"}
                    continue
                rows = element_rows(fd.getElement("FWD_CURVE"))
                points, columns = points_from_rows(rows)
                out[t] = {"points": points, "columns": columns,
                          "error": "" if points else f"FWD_CURVE table not parseable; columns={columns}"}
        if ev.eventType() == blpapi.Event.RESPONSE and event_is_ours:
            break
    return out


def historical_points_by_day(tenor_series: Dict[str, Dict[str, Dict[str, object]]],
                             tenor_tickers: Dict[str, str]) -> Dict[str, List[Point]]:
    """Turn a `data.bloomberg.pull_marks.fetch_historical_series` result for a pair's
    standard-tenor outright tickers (`tenor_tickers`: {tenor label -> bbg_ticker}, fields
    PX_LAST + SETTLE_DT) into `{date_iso: [(settle_date, outright), ...]}` -- one curve per
    historical day, ready for `outright_for_date` exactly as the live path's bulk-table
    points are (see module docstring for the "why not just historical FWD_CURVE" backdrop
    and the SETTLE_DT-via-HistoricalDataRequest assumption this rests on).

    A tenor missing either PX_LAST or SETTLE_DT on a given day (or with a non-positive
    price -- same filter `points_from_rows` applies) is simply absent from that day's
    points, never guessed; a day with no usable tenor at all is simply absent from the
    returned dict (callers see an empty curve, not a fabricated one)."""
    by_day: Dict[str, List[Point]] = {}
    for tenor, ticker in tenor_tickers.items():
        series = tenor_series.get(ticker, {})
        for day_iso, row in series.items():
            px, sd = row.get("PX_LAST"), row.get("SETTLE_DT")
            if px is None or sd is None:
                continue
            settle = _to_date(sd)
            if settle is None:
                continue
            try:
                value = float(px)
            except (TypeError, ValueError):
                continue
            if value <= 0:
                continue
            by_day.setdefault(day_iso, []).append((settle, value))
    for points in by_day.values():
        points.sort()
    return by_day
