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
    OUTRIGHTS format. Plain Python out; no blpapi objects escape."""
    req = service.createRequest("ReferenceDataRequest")
    for t in tickers:
        req.getElement("securities").appendValue(t)
    req.getElement("fields").appendValue("FWD_CURVE")
    ov = req.getElement("overrides").appendElement()
    ov.setElement("fieldId", "FWD_CURVE_QUOTE_FORMAT")
    ov.setElement("value", "OUTRIGHTS")
    session.sendRequest(req)
    out: Dict[str, dict] = {t: {"points": [], "columns": [], "error": "no response for ticker"} for t in tickers}
    while True:
        ev = session.nextEvent(timeout_ms)
        if ev.eventType() == blpapi.Event.TIMEOUT:
            for t in tickers:
                if out[t]["error"] == "no response for ticker":
                    out[t]["error"] = "TIMEOUT"
            break
        for msg in ev:
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
        if ev.eventType() == blpapi.Event.RESPONSE:
            break
    return out
