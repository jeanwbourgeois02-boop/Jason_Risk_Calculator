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

`historical_curve` (2026-09-21) is what the backfill uses now. SETTLE_DT is a static
reference field and HistoricalDataRequest does not serve it, so `historical_points_by_day`
dropped every tenor of every past day and no past forward was ever written. It builds ONE
day's curve from that day's tenor PX_LAST values and that day's SPOT close:
  * pillar dates: Bloomberg's own SETTLE_DT when the row carries one, otherwise computed by
    market convention (`spot_date_for`, `tenor_settle_date`) and flagged as computed, so the
    caller never writes a value at a computed date as Bloomberg's own quote;
  * unit: the live tenor path (pull_marks.fetch_tenor_points) documents these tickers'
    PX_LAST as forward POINTS (outright = spot + points / the pair's points divisor,
    pull_marks.fetch_points_scales), which this module used to read as outrights. Since
    2026-09-21 the values are the 15:00 New York close from intraday bars, under the same
    PX_LAST key (pull_marks.fetch_intraday_close_series). Neither is verified on a terminal
    (docs/open-questions.md item 28, which gives the test used here: a PX_LAST of the same
    order of magnitude as spot is an outright, anything else is points) -- `tenor_unit`.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta
from typing import Dict, FrozenSet, List, Optional, Tuple

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
    returned dict (callers see an empty curve, not a fabricated one).

    The backfill no longer calls this (2026-09-21): it needs SETTLE_DT on every row and
    reads PX_LAST as an outright -- see `historical_curve`."""
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


# --------------------------------------------------------------------------- historical curve (2026-09-21)
UNIT_OUTRIGHT = "OUTRIGHT"
UNIT_POINTS = "POINTS"

# Spot settles T+1 for these pairs by market convention, T+2 for everything else.
_SPOT_LAG_ONE_DAY = frozenset({"USDCAD", "USDTRY", "USDPHP", "USDRUB"})

_NO_HOLIDAYS: FrozenSet[str] = frozenset()
_TENOR_RE = re.compile(r"(\d+)([WMY])")


def _is_good_day(d: date, holidays: FrozenSet[str]) -> bool:
    return d.weekday() < 5 and d.isoformat() not in holidays


def spot_date_for(day: date, pair: str = "", holidays: FrozenSet[str] = _NO_HOLIDAYS) -> date:
    """The spot value date of a trade dealt on `day`: `day` + 2 weekdays (+ 1 for the T+1
    pairs), then rolled forward off a holiday. For a T+2 pair with no holiday in the way
    this is engine.ladder.usd_marks.spot_date, the app's existing spot-date rule. A holiday
    on the day in between does not count against the lag (the convention for a US holiday);
    only the spot date itself must be a good day. `holidays` is config/holidays.txt's set
    (engine.pnl.calendar.load_holidays): the app has no per-currency calendars, so a date
    computed here can sit a day away from Bloomberg's around a local holiday -- one reason
    a value at a computed date is never written as Bloomberg's own quote."""
    lag = 1 if pair in _SPOT_LAG_ONE_DAY else 2
    d, added = day, 0
    while added < lag:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    while not _is_good_day(d, holidays):
        d += timedelta(days=1)
    return d


def _is_last_good_day_of_month(d: date, holidays: FrozenSet[str]) -> bool:
    nxt = d + timedelta(days=1)
    while nxt.month == d.month:
        if _is_good_day(nxt, holidays):
            return False
        nxt += timedelta(days=1)
    return True


def tenor_settle_date(spot: date, tenor: str, holidays: FrozenSet[str] = _NO_HOLIDAYS) -> Optional[date]:
    """Value date of a standard tenor counted from the spot date `spot`, by market
    convention: 'SP' is the spot date; a week tenor is spot + 7n days rolled FOLLOWING; a
    month / year tenor is spot + n months rolled MODIFIED FOLLOWING, with the end-of-month
    rule (a spot date that is the last good day of its month gives the last good day of
    the target month). None for a label this does not know (ON, TN, ...)."""
    tenor = tenor.upper()
    if tenor == "SP":
        return spot
    m = _TENOR_RE.fullmatch(tenor)
    if m is None:
        return None
    n, unit = int(m.group(1)), m.group(2)
    if unit == "W":
        d = spot + timedelta(weeks=n)
        while not _is_good_day(d, holidays):
            d += timedelta(days=1)
        return d
    month_index = spot.month - 1 + n * (12 if unit == "Y" else 1)
    year, month = spot.year + month_index // 12, month_index % 12 + 1
    last = calendar.monthrange(year, month)[1]
    if _is_last_good_day_of_month(spot, holidays):
        d = date(year, month, last)
        while not _is_good_day(d, holidays):
            d -= timedelta(days=1)
        return d
    target = date(year, month, min(spot.day, last))
    d = target
    while not _is_good_day(d, holidays):
        d += timedelta(days=1)
    if d.month != target.month:                 # modified following: never into the next month
        d = target
        while not _is_good_day(d, holidays):
            d -= timedelta(days=1)
    return d


def tenor_unit(values: List[float], spot: float) -> str:
    """OUTRIGHT when every tenor value lies within [0.5, 2] x spot, else POINTS -- the
    test docs/open-questions.md item 28 gives ("a PX_LAST in the same order of magnitude
    as spot means it is an outright, not points"). An outright out to one year never
    leaves that band; a run of forward points grows roughly with the tenor (1W to 1Y is
    fifty-fold) and is often negative, so it cannot sit inside it as a whole."""
    return UNIT_OUTRIGHT if values and all(0.5 * spot <= v <= 2.0 * spot for v in values) else UNIT_POINTS


def historical_curve(day: date, tenor_rows: Dict[str, Dict[str, object]], spot: Optional[float],
                     scale: Optional[float] = None, pair: str = "",
                     holidays: FrozenSet[str] = _NO_HOLIDAYS) -> dict:
    """ONE past day's forward curve for `pair`, from that day's standard-tenor rows
    (`tenor_rows`: {tenor label: {'PX_LAST': value, 'SETTLE_DT': date, if Bloomberg sent
    one}}) and that day's SPOT close. Returns
    {'points': [(settle_date, outright), ...] sorted, 'own_dates': {settle dates that are
    Bloomberg's own SETTLE_DT}, 'unit': OUTRIGHT | POINTS | '', 'reason': why there are no
    points, else ''}.

    POINTS: outright = spot + points / scale, exactly pull_marks.outright_from_points
    (`scale` = the pair's points divisor, pull_marks.fetch_points_scales: FWD_POINTS_SCALE
    as is, else 10 ** FWD_SCALE); the first pillar is the spot date at spot
    itself (forward points are zero there by definition, so the SP ticker's own value is
    not used); a negative or zero points value is a real quote and is kept. Linear
    interpolation between these outrights is the live path's linear interpolation in
    points, since spot and scale are the same for every pillar. OUTRIGHT: each positive
    value is used as it came. Nothing is guessed: no SPOT that day, or points with no
    scale, gives no points and a reason."""
    out = {"points": [], "own_dates": set(), "unit": "", "reason": ""}
    quotes: Dict[str, float] = {}
    for tenor, row in tenor_rows.items():
        try:
            quotes[tenor] = float((row or {}).get("PX_LAST"))
        except (TypeError, ValueError):
            continue
    label = pair or "this pair"
    if not quotes:
        out["reason"] = f"Bloomberg returned no forward tenor prices for {label} on {day.isoformat()}"
        return out
    if spot is None or spot <= 0:
        out["reason"] = f"{label} has no SPOT close on {day.isoformat()}, so its forward tenors cannot be read"
        return out
    unit = out["unit"] = tenor_unit(list(quotes.values()), spot)
    if unit == UNIT_POINTS and (scale is None or scale <= 0):
        # The divisor comes from pull_marks.fetch_points_scales (FWD_POINTS_SCALE as is,
        # else 10 ** FWD_SCALE); the backfill replaces this sentence with one that also
        # says what each field sent (pull_marks.describe_scale).
        out["reason"] = (f"Bloomberg returned forward points for {label} but neither FWD_POINTS_SCALE nor "
                         "FWD_SCALE, so they cannot be converted to outrights")
        return out

    def own_date(tenor: str) -> Optional[date]:
        return _to_date((tenor_rows.get(tenor) or {}).get("SETTLE_DT"))

    spot_day = own_date("SP") or spot_date_for(day, pair, holidays)
    by_date: Dict[date, float] = {}
    if unit == UNIT_POINTS:
        by_date[spot_day] = float(spot)
        if own_date("SP") is not None:
            out["own_dates"].add(spot_day)
    for tenor, value in quotes.items():
        if unit == UNIT_POINTS and tenor.upper() == "SP":
            continue
        settle = own_date(tenor)
        if settle is not None:
            is_own = True
        else:
            settle, is_own = tenor_settle_date(spot_day, tenor, holidays), False
        outright = spot + value / scale if unit == UNIT_POINTS else value
        if settle is None or outright <= 0 or settle in by_date:
            continue
        by_date[settle] = outright
        if is_own:
            out["own_dates"].add(settle)
    out["points"] = sorted(by_date.items())
    if not out["points"]:
        out["reason"] = f"no usable forward tenor for {label} on {day.isoformat()}"
    return out
