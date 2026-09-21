#!/usr/bin/env python
"""Standalone Bloomberg diagnostic for the risk-monitor cash ladder.

Portable: Python 3 standard library + (optionally) blpapi. Imports nothing from the
repository, so it runs on the Bloomberg computer with only this file and the SQLite
database. READ-ONLY: it opens the database in read-only mode and never writes marks.
No mock, random or fallback prices: a ticker either returns a Bloomberg value or is
reported FAILED with the exact error text.

Usage:
    py -3 tools\\bloomberg_terminal_probe.py --once
    py -3 tools\\bloomberg_terminal_probe.py --once --db C:\\path\\risk.db --host localhost --port 8194

Distinct from tools/bbg_diagnostics.py (the in-app diagnostics module, which imports the
repo and needs the app's own DB/session wiring): this file is the zero-repo-imports
version meant to run standalone on the Bloomberg terminal machine with only this file
and the SQLite database copied over.

Checks (all required for exit code 0):
    1  python        version / executable
    2  blpapi        import
    3  tcp           localhost:8194 connectivity
    4  session       blpapi Session start
    5  service       //blp/refdata open
    6  database      FX instruments with open legs + their settle dates
    7  spot          PX_LAST for every open pair (ReferenceDataRequest)
    8  forward       outright per pair at its first open settle date, from the bulk
                     FWD_CURVE table (FWD_CURVE_QUOTE_FORMAT=OUTRIGHTS): exact tenor row,
                     or linear interpolation between tenors / from live spot (labelled
                     BBG_INTERP). Never extrapolated beyond the last tenor.
Informational (2026-09-21, not required for exit 0; each prints what came back):
    fwdscale         FWD_POINTS_SCALE and FWD_SCALE together on EURUSD and USDJPY: the
                     forward-points divisor the past-close forwards need
    intraday         one IntradayBarRequest (EURUSD Curncy, BID, hourly, the last business
                     day, 14:00-15:00 New York): the 15:00 New York close the app now uses
Reports: reports/bloomberg_diagnostic_<timestamp>.json and .txt (next to this file's
parent directory unless --out is given).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import sqlite3
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REQUIRED = ["python", "blpapi", "tcp", "session", "service", "database", "spot", "forward"]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class Report:
    def __init__(self):
        self.started = now_iso()
        self.checks: dict = {}
        self.tickers: list = []
        self.notes: list = []

    def check(self, name: str, ok: bool, detail: str = "", **extra):
        self.checks[name] = {"ok": bool(ok), "detail": detail, **extra}
        label = "OK    " if ok else ("FAILED" if extra.get("required", True) else "CHECK ")
        print(f"[{label}] {name:9s} {detail}")

    def ticker(self, **row):
        self.tickers.append(row)
        val = "" if row.get("value") is None else f"{row['value']}"
        print(f"    [{row['status']:6s}] {row['instrument_id']:8s} {row['mark_type']:12s} {row['ticker']:22s} "
              f"{row.get('settle_date', ''):10s} {val:>16s}  {row.get('detail', '')}")

    @property
    def all_required_ok(self) -> bool:
        return all(self.checks.get(n, {}).get("ok") for n in REQUIRED)

    def to_dict(self) -> dict:
        return {"started": self.started, "finished": now_iso(), "all_required_ok": self.all_required_ok,
                "required_checks": REQUIRED, "checks": self.checks, "tickers": self.tickers, "notes": self.notes}

    def to_text(self) -> str:
        lines = [f"Bloomberg diagnostic  started {self.started}  finished {now_iso()}",
                 f"RESULT: {'ALL REQUIRED CHECKS PASSED' if self.all_required_ok else 'BLOOMBERG UNAVAILABLE OR INCOMPLETE'}", ""]
        for n in REQUIRED:
            c = self.checks.get(n, {"ok": False, "detail": "not run"})
            lines.append(f"[{'OK' if c['ok'] else 'FAILED':6s}] {n:9s} {c.get('detail', '')}")
        info = [n for n in self.checks if n not in REQUIRED]
        if info:
            lines.append("")
            lines.append("Informational (not required for exit 0):")
            for n in info:
                c = self.checks[n]
                lines.append(f"[{'OK' if c['ok'] else 'CHECK':6s}] {n:9s} {c.get('detail', '')}")
        if self.tickers:
            lines += ["", f"{'status':7s} {'pair':8s} {'mark':12s} {'ticker':22s} {'settle':10s} {'value':>16s}  "
                          f"{'request':22s} {'source':16s} {'snapped_at':25s} detail"]
            for t in self.tickers:
                val = "" if t.get("value") is None else f"{t['value']}"
                lines.append(f"{t['status']:7s} {t['instrument_id']:8s} {t['mark_type']:12s} {t['ticker']:22s} "
                             f"{t.get('settle_date', ''):10s} {val:>16s}  {t.get('request_type', ''):22s} "
                             f"{t.get('source', ''):16s} {t.get('snapped_at', ''):25s} {t.get('detail', '')}")
        if self.notes:
            lines += ["", "Notes:"] + [f"  - {n}" for n in self.notes]
        return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- checks 1-3
def check_python(rep: Report) -> None:
    ok = sys.version_info >= (3, 9)
    rep.check("python", ok, f"{platform.python_version()} at {sys.executable}",
              version=platform.python_version(), executable=sys.executable, platform=platform.platform())


def check_blpapi(rep: Report):
    try:
        import blpapi  # type: ignore
        rep.check("blpapi", True, f"blpapi {getattr(blpapi, '__version__', 'unknown')} imported", version=getattr(blpapi, "__version__", None))
        return blpapi
    except Exception as exc:  # ImportError or DLL load failure
        rep.check("blpapi", False, f"import failed: {exc!r}. Install: py -3 -m pip install --index-url="
                                   f"https://blpapi.bloomberg.com/repository/releases/python/simple/ blpapi")
        return None


def check_tcp(rep: Report, host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2.0):
            rep.check("tcp", True, f"{host}:{port} accepted a connection")
            return True
    except OSError as exc:
        rep.check("tcp", False, f"{host}:{port} refused: {exc}. Is the Bloomberg Terminal logged in on this computer?")
        return False


# --------------------------------------------------------------------------- checks 4-5
def open_session(rep: Report, blpapi, host: str, port: int):
    try:
        opts = blpapi.SessionOptions()
        opts.setServerHost(host)
        opts.setServerPort(port)
        session = blpapi.Session(opts)
        if not session.start():
            rep.check("session", False, "Session.start() returned False")
            return None, None
        rep.check("session", True, f"session started on {host}:{port}")
    except Exception as exc:
        rep.check("session", False, f"exception: {exc!r}")
        return None, None
    try:
        if not session.openService("//blp/refdata"):
            rep.check("service", False, "openService('//blp/refdata') returned False")
            return session, None
        rep.check("service", True, "//blp/refdata opened")
        return session, session.getService("//blp/refdata")
    except Exception as exc:
        rep.check("service", False, f"exception: {exc!r}")
        return session, None


# --------------------------------------------------------------------------- check 6
_OPEN_FX_SQL = """
SELECT i.instrument_id, i.bbg_ticker, i.base_ccy, i.quote_ccy, MIN(l.settle_date) AS first_settle, COUNT(*) AS legs
FROM trade_legs l JOIN trades t USING (trade_id) JOIN instruments i USING (instrument_id)
WHERE i.asset_class = 'FX' AND l.settle_date >= :as_of
GROUP BY i.instrument_id, i.bbg_ticker, i.base_ccy, i.quote_ccy ORDER BY i.instrument_id
"""


def read_requests(rep: Report, db_path: Path, as_of: str | None) -> list:
    if not db_path.exists():
        rep.check("database", False, f"{db_path} does not exist")
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)  # read-only
        try:
            # 2026-09-17 ("no bnp fall back"): default to today, not MAX(positions.as_of_date)
            # -- the retired BNP snapshot date, which could silently drop every trade dated
            # after it (same fix as data/bloomberg/live.py::pull_once, see its docstring).
            if as_of is None:
                as_of = date.today().isoformat()
            rows = conn.execute(_OPEN_FX_SQL, {"as_of": as_of}).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        rep.check("database", False, f"sqlite error: {exc}")
        return []
    reqs = [{"instrument_id": r[0], "ticker": r[1], "base": r[2], "quote": r[3], "settle_date": r[4], "legs": r[5]} for r in rows]
    rep.check("database", bool(reqs), f"{len(reqs)} FX pairs with open legs as of {as_of} in {db_path}"
              if reqs else f"no FX pairs with open legs as of {as_of} in {db_path}", as_of=as_of, pairs=[r["instrument_id"] for r in reqs])
    return reqs


# --------------------------------------------------------------------------- Bloomberg requests
def reference_request(blpapi, session, service, tickers, fields, overrides=None, timeout_ms=15000):
    """{ticker: {field: value}} plus per-ticker error text; plain Python only."""
    req = service.createRequest("ReferenceDataRequest")
    for t in tickers:
        req.getElement("securities").appendValue(t)
    for f in fields:
        req.getElement("fields").appendValue(f)
    if overrides:
        ov = req.getElement("overrides")
        for k, v in overrides.items():
            o = ov.appendElement()
            o.setElement("fieldId", k)
            o.setElement("value", v)
    session.sendRequest(req)
    out, errors = {}, {}
    while True:
        ev = session.nextEvent(timeout_ms)
        if ev.eventType() == blpapi.Event.TIMEOUT:
            for t in tickers:
                errors.setdefault(t, "TIMEOUT waiting for response")
            break
        for msg in ev:
            if not msg.hasElement("securityData"):
                if msg.hasElement("responseError"):
                    err = msg.getElement("responseError")
                    for t in tickers:
                        errors[t] = f"responseError: {err}"
                continue
            for sd in msg.getElement("securityData").values():
                t = sd.getElementAsString("security")
                if sd.hasElement("securityError"):
                    errors[t] = "securityError: " + sd.getElement("securityError").getElementAsString("message")
                    continue
                fe = []
                if sd.hasElement("fieldExceptions"):
                    for x in sd.getElement("fieldExceptions").values():
                        fe.append(f"{x.getElementAsString('fieldId')}: {x.getElement('errorInfo').getElementAsString('message')}")
                fd = sd.getElement("fieldData")
                vals = {}
                for f in fields:
                    if fd.hasElement(f):
                        el = fd.getElement(f)
                        try:
                            vals[f] = el.getValueAsFloat() if el.numValues() == 1 and not el.isArray() else str(el)
                        except Exception:
                            vals[f] = str(el)
                out[t] = vals
                if fe:
                    errors[t] = "fieldExceptions: " + "; ".join(fe)
        if ev.eventType() == blpapi.Event.RESPONSE:
            break
    return out, errors


def check_prices(rep: Report, blpapi, session, service, reqs: list) -> None:
    snapped = now_iso()
    # ---- spot
    tickers = [r["ticker"] for r in reqs]
    try:
        data, errors = reference_request(blpapi, session, service, tickers, ["PX_LAST"])
    except Exception as exc:
        rep.check("spot", False, f"request raised: {exc!r}")
        rep.notes.append(traceback.format_exc())
        data, errors = {}, {t: f"request raised: {exc!r}" for t in tickers}
    spot_ok = 0
    for r in reqs:
        v = data.get(r["ticker"], {}).get("PX_LAST")
        ok = isinstance(v, float)
        spot_ok += ok
        rep.ticker(status="OK" if ok else "FAILED", instrument_id=r["instrument_id"], mark_type="SPOT", ticker=r["ticker"],
                   settle_date="", value=v if ok else None, request_type="ReferenceDataRequest PX_LAST",
                   source="BBG_BFXFORWARD" if ok else "", snapped_at=snapped if ok else "",
                   detail="" if ok else errors.get(r["ticker"], "no PX_LAST in response"))
    if "spot" not in rep.checks:
        rep.check("spot", spot_ok == len(reqs), f"{spot_ok}/{len(reqs)} pairs returned PX_LAST")
    # ---- forward: bulk FWD_CURVE table (OUTRIGHTS) per pair, outright at the pair's first open settle date
    fwd_ok = 0
    spot_by_pair = {r["instrument_id"]: data.get(r["ticker"], {}).get("PX_LAST") for r in reqs}
    try:
        curves = fwd_curve_request(blpapi, session, service, tickers)
    except Exception as exc:
        curves = {t: {"points": [], "columns": [], "error": f"request raised: {exc!r}"} for t in tickers}
        rep.notes.append(traceback.format_exc())
    columns_seen = next((c["columns"] for c in curves.values() if c.get("columns")), [])
    if columns_seen:
        rep.notes.append(f"FWD_CURVE table columns on this terminal: {columns_seen}")
    skipped = 0
    for r in reqs:
        settle = r["settle_date"]
        if date.fromisoformat(settle) < date.today():
            skipped += 1
            rep.ticker(status="SKIP", instrument_id=r["instrument_id"], mark_type="FWD_OUTRIGHT", ticker=r["ticker"],
                       settle_date=settle, value=None, request_type="none",
                       source="", snapped_at="",
                       detail=f"settle date already past on {date.today()}: trade has settled, no forward to price")
            continue
        curve = curves.get(r["ticker"], {"points": [], "error": "no curve returned"})
        if not curve["points"]:
            rep.ticker(status="FAILED", instrument_id=r["instrument_id"], mark_type="FWD_OUTRIGHT", ticker=r["ticker"],
                       settle_date=settle, value=None, request_type="ReferenceDataRequest FWD_CURVE (OUTRIGHTS, bulk)",
                       source="", snapped_at="", detail=curve.get("error", "no points"))
            continue
        spot = spot_by_pair.get(r["instrument_id"])
        value, how = outright_for_date(curve["points"], date.fromisoformat(settle),
                                       spot if isinstance(spot, float) else None, date.today())
        if value is None:
            rep.ticker(status="FAILED", instrument_id=r["instrument_id"], mark_type="FWD_OUTRIGHT", ticker=r["ticker"],
                       settle_date=settle, value=None, request_type="ReferenceDataRequest FWD_CURVE (OUTRIGHTS, bulk)",
                       source="", snapped_at="",
                       detail=f"{settle} outside curve {curve['points'][0][0]}..{curve['points'][-1][0]} "
                              f"({len(curve['points'])} tenors)")
            continue
        fwd_ok += 1
        rep.ticker(status="OK", instrument_id=r["instrument_id"], mark_type="FWD_OUTRIGHT", ticker=r["ticker"],
                   settle_date=settle, value=round(value, 8), request_type="ReferenceDataRequest FWD_CURVE (OUTRIGHTS, bulk)",
                   source="BBG_BFXFORWARD" if how == "EXACT" else "BBG_INTERP", snapped_at=snapped,
                   detail=f"{how.lower()} from {len(curve['points'])} tenor rows "
                          f"({curve['points'][0][0]}..{curve['points'][-1][0]})")
    needed = len(reqs) - skipped
    rep.check("forward", fwd_ok == needed,
              f"{fwd_ok}/{needed} pairs returned a forward outright"
              + (f" ({skipped} skipped: settle date already past, nothing to price)" if skipped else ""))


# --------------------------------------------------------------------------- 2026-09-21 probes (informational)
# Two things the app now relies on and nobody has seen answered on a terminal. Neither is
# required for exit 0; each prints exactly what came back so a paste settles it.
SCALE_FIELDS = ["FWD_POINTS_SCALE", "FWD_SCALE"]
CLOSE_HOUR_NY = 15          # the official close (user decision 2026-09-21), as data/bloomberg/pull_marks.py


def check_fwd_scale(rep: Report, blpapi, session, service) -> None:
    """The forward-points divisor. The Bloomberg PC returned nothing for FWD_POINTS_SCALE,
    so past-close forwards could not be built; the app now asks for FWD_POINTS_SCALE (the
    divisor itself) and FWD_SCALE (decimal places, divisor = 10 ** n) in ONE request. Asked
    here on a 4-decimal pair and a 2-decimal pair: expect FWD_SCALE 4 and 2 (or
    FWD_POINTS_SCALE 10000 and 100)."""
    tickers = ["EURUSD Curncy", "USDJPY Curncy"]
    try:
        data, errors = reference_request(blpapi, session, service, tickers, SCALE_FIELDS)
    except Exception as exc:
        rep.check("fwdscale", False, f"request raised: {exc!r}", required=False)
        return
    parts, usable = [], 0
    for t in tickers:
        vals = data.get(t, {})
        divisor = None
        if isinstance(vals.get("FWD_POINTS_SCALE"), float) and vals["FWD_POINTS_SCALE"] > 0:
            divisor = f"divisor {vals['FWD_POINTS_SCALE']:g} from FWD_POINTS_SCALE"
        elif isinstance(vals.get("FWD_SCALE"), float) and vals["FWD_SCALE"] == int(vals["FWD_SCALE"]) and 0 <= vals["FWD_SCALE"] <= 8:
            divisor = f"divisor {10 ** int(vals['FWD_SCALE']):g} from FWD_SCALE"
        usable += divisor is not None
        parts.append(f"{t}: sent {vals or 'nothing'}" + (f" [{errors[t]}]" if t in errors else "")
                     + f" -> {divisor or 'NO usable divisor'}")
    rep.check("fwdscale", usable == len(tickers), "; ".join(parts), required=False, fields=SCALE_FIELDS,
              values={t: data.get(t, {}) for t in tickers}, errors=errors)


def check_intraday_close(rep: Report, blpapi, session, service) -> None:
    """ONE IntradayBarRequest: EURUSD Curncy, BID, hourly, the last business day before
    today, 14:00-15:00 New York. Past FX closes are read this way (the 15:00 New York
    close; Bloomberg's daily PX_LAST is the 17:00 one). Confirms the request shape and the
    event type work for a Curncy ticker here, and shows the bar's own time."""
    try:
        from zoneinfo import ZoneInfo
        ny = ZoneInfo("America/New_York")
    except Exception as exc:
        rep.check("intraday", False, f"America/New_York not resolvable ({exc!r}): py -3 -m pip install tzdata", required=False)
        return
    day = datetime.now(ny).date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    end = datetime(day.year, day.month, day.day, CLOSE_HOUR_NY, 0, tzinfo=ny).astimezone(timezone.utc)
    start = end - timedelta(hours=1)
    bars, error = [], ""
    try:
        req = service.createRequest("IntradayBarRequest")
        req.set("security", "EURUSD Curncy")
        req.set("eventType", "BID")
        req.set("interval", 60)
        req.set("startDateTime", start.replace(tzinfo=None))      # UTC, naive
        req.set("endDateTime", end.replace(tzinfo=None))
        req.set("gapFillInitialBar", True)
        session.sendRequest(req)
        while True:
            ev = session.nextEvent(15000)
            if ev.eventType() == blpapi.Event.TIMEOUT:
                error = "TIMEOUT waiting for response"
                break
            for msg in ev:
                if msg.hasElement("responseError"):
                    error = f"responseError: {msg.getElement('responseError')}"
                    continue
                if not msg.hasElement("barData"):
                    continue
                ticks = msg.getElement("barData").getElement("barTickData")
                for i in range(ticks.numValues()):
                    bar = ticks.getValueAsElement(i)
                    bars.append({"time": str(bar.getElement("time").getValue()),
                                 "close": bar.getElement("close").getValue()})
            if ev.eventType() == blpapi.Event.RESPONSE:
                break
    except Exception as exc:
        error = f"request raised: {exc!r}"
    asked = f"EURUSD Curncy BID 60-minute bars {start:%Y-%m-%d %H:%M}-{end:%H:%M} UTC (= 14:00-15:00 New York on {day})"
    detail = f"{asked}: " + (f"{len(bars)} bar(s) {bars}" if bars else "NO bar returned") + (f" [{error}]" if error else "")
    rep.check("intraday", bool(bars) and not error, detail, required=False, bars=bars, error=error,
              expected_bar_start_utc=start.isoformat())


# --------------------------------------------------------------------------- FWD_CURVE bulk parsing
# Standalone copy of data/bloomberg/fwd_curve.py (this file must not import the repo).
_MID_KEYS = ("MID", "OUTRIGHT", "RATE", "PX_MID", "VALUE")
_DATE_KEYS = ("SETTLE", "DATE", "MATURITY")


def _to_date(v):
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


def points_from_rows(rows):
    points, columns = [], []
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
        numeric = {k: float(v) for k, v in row.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
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


def outright_for_date(points, target, spot=None, spot_date=None):
    for d, v in points:
        if d == target:
            return v, "EXACT"
    before = [(d, v) for d, v in points if d < target]
    after = [(d, v) for d, v in points if d > target]
    if before and after:
        (d0, v0), (d1, v1) = before[-1], after[0]
        return v0 + (target - d0).days / (d1 - d0).days * (v1 - v0), "INTERP"
    if not before and after and spot is not None and spot_date is not None and spot_date < target:
        d1, v1 = after[0]
        return spot + (target - spot_date).days / (d1 - spot_date).days * (v1 - spot), "INTERP_FROM_SPOT"
    return None, ""


def _element_rows(bulk):
    rows = []
    for i in range(bulk.numValues()):
        row_el = bulk.getValueAsElement(i)
        row = {}
        for j in range(row_el.numElements()):
            sub = row_el.getElement(j)
            try:
                row[str(sub.name())] = sub.getValue() if sub.numValues() == 1 else str(sub)
            except Exception:
                row[str(sub.name())] = str(sub)
        rows.append(row)
    return rows


def fwd_curve_request(blpapi, session, service, tickers, timeout_ms=15000):
    """{ticker: {points, columns, error}} from FWD_CURVE with FWD_CURVE_QUOTE_FORMAT=OUTRIGHTS."""
    req = service.createRequest("ReferenceDataRequest")
    for t in tickers:
        req.getElement("securities").appendValue(t)
    req.getElement("fields").appendValue("FWD_CURVE")
    ov = req.getElement("overrides").appendElement()
    ov.setElement("fieldId", "FWD_CURVE_QUOTE_FORMAT")
    ov.setElement("value", "OUTRIGHTS")
    session.sendRequest(req)
    out = {t: {"points": [], "columns": [], "error": "no response for ticker"} for t in tickers}
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
                    out[t] = {"points": [], "columns": [], "error": ("fieldExceptions: " + "; ".join(fe)) if fe else "FWD_CURVE absent"}
                    continue
                rows = _element_rows(fd.getElement("FWD_CURVE"))
                points, columns = points_from_rows(rows)
                out[t] = {"points": points, "columns": columns,
                          "error": "" if points else f"FWD_CURVE table not parseable; columns={columns}; first row={rows[0] if rows else None}"}
        if ev.eventType() == blpapi.Event.RESPONSE:
            break
    return out


# --------------------------------------------------------------------------- ledger + feed (informational, read-only)
def check_ledger(rep: Report, db_path: Path) -> None:
    """P&L ledger state: tables present, snapshot history, realised trades, settled trades
    that are not realisable (no spot on/before settle date). Informational: not required
    for exit 0, but explains 'Unavailable' period cards in the app."""
    if not db_path.exists():
        rep.check("ledger", False, "database missing", required=False)
        return
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "realised_pnl" not in tables:
                rep.check("ledger", False, "ledger tables not created yet: launch the app once (it adds them), "
                                           "then run the feed", required=False)
                return
            realised = conn.execute("SELECT COUNT(*), COALESCE(SUM(pnl_usd),0) FROM realised_pnl").fetchone()
            today = date.today().isoformat()
            settled_unrealised = conn.execute(
                "SELECT DISTINCT t.trade_id, t.instrument_id, l.settle_date FROM trades t JOIN trade_legs l USING(trade_id) "
                "WHERE t.product IN ('FX_SPOT','FX_FWD','FX_SWAP') AND l.settle_date < ? "
                "AND t.trade_id NOT IN (SELECT trade_id FROM realised_pnl) ORDER BY l.settle_date", (today,)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        rep.check("ledger", False, f"sqlite error: {exc}", required=False)
        return
    detail = (f"{realised[0]} realised trade(s) = {realised[1]:,.0f} USD; "
              + f"{len(settled_unrealised)} settled-but-unrealised trade(s)")
    ok = not settled_unrealised
    rep.check("ledger", ok, detail, required=False, realised_trades=realised[0], realised_usd=realised[1],
              settled_unrealised=[{"trade_id": t, "pair": p, "settle_date": s} for t, p, s in settled_unrealised])
    for t, p, s in settled_unrealised[:10]:
        rep.notes.append(f"settled, not realised: {t} {p} settled {s} (needs an official SPOT on/before that date)")


def check_feed_status(rep: Report, db_path: Path) -> None:
    """Last live-feed status file written by the app (data/bloomberg/live.py)."""
    p = db_path.with_name(db_path.name + ".bloomberg_status.json")
    if not p.exists():
        rep.check("feed", False, "no feed status file: the app has not run its Bloomberg feed on this database yet",
                  required=False)
        return
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        rep.check("feed", False, f"status file unreadable: {exc}", required=False)
        return
    detail = (f"time {st.get('time')} connected={st.get('connected')} written={st.get('written', 0)} "
              f"failed={st.get('failed', 0)} skipped={st.get('skipped', 0)} {st.get('reason', '')}")
    led = st.get("ledger")
    if led:
        detail += f" | ledger: {led}"
    rep.check("feed", bool(st.get("connected")) and not st.get("failed"), detail, required=False)


# --------------------------------------------------------------------------- main
def default_db() -> Path:
    env = os.environ.get("RISK_DB")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[1] / "data" / "raw" / "risk.db"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Standalone Bloomberg connectivity and pricing diagnostic (read-only).")
    p.add_argument("--once", action="store_true", help="run the diagnostic once (default behaviour)")
    p.add_argument("--db", default=None, help="SQLite database (default: RISK_DB or data/raw/risk.db)")
    p.add_argument("--as-of", default=None, help="as-of date for open legs (default: today)")
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=8194)
    p.add_argument("--out", default=None, help="report directory (default: <repo>/reports)")
    args = p.parse_args(argv)

    rep = Report()
    db_path = Path(args.db) if args.db else default_db()
    out_dir = Path(args.out) if args.out else Path(__file__).resolve().parents[1] / "reports"
    print(f"Bloomberg diagnostic {rep.started}  db={db_path}  target={args.host}:{args.port}")

    check_python(rep)
    blpapi = check_blpapi(rep)
    tcp_ok = check_tcp(rep, args.host, args.port)
    reqs = read_requests(rep, db_path, args.as_of)
    session = service = None
    if blpapi is not None and tcp_ok:
        session, service = open_session(rep, blpapi, args.host, args.port)
    else:
        why = "blpapi not importable" if blpapi is None else "no TCP connectivity"
        rep.check("session", False, f"skipped: {why}")
        rep.check("service", False, f"skipped: {why}")
    if service is not None and reqs:
        try:
            check_prices(rep, blpapi, session, service, reqs)
        except Exception as exc:
            rep.check("spot", False, f"unhandled: {exc!r}")
            rep.check("forward", False, f"unhandled: {exc!r}")
            rep.notes.append(traceback.format_exc())
    else:
        why = "no Bloomberg service" if service is None else "no instruments to request"
        rep.check("spot", False, f"skipped: {why}")
        rep.check("forward", False, f"skipped: {why}")
    if service is not None:
        # 2026-09-21, informational: the points-divisor fields and the 15:00 New York close bar
        for probe in (check_fwd_scale, check_intraday_close):
            try:
                probe(rep, blpapi, session, service)
            except Exception as exc:
                rep.check(probe.__name__.replace("check_", ""), False, f"unhandled: {exc!r}", required=False)
    if session is not None:
        try:
            session.stop()
        except Exception:
            pass
    check_ledger(rep, db_path)
    check_feed_status(rep, db_path)

    rep.notes.append("Read-only run: no database, marks, UI or valuation changes were made.")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"bloomberg_diagnostic_{stamp}.json"
    txt_path = out_dir / f"bloomberg_diagnostic_{stamp}.txt"
    json_path.write_text(json.dumps(rep.to_dict(), indent=2, default=str), encoding="utf-8")
    txt_path.write_text(rep.to_text(), encoding="utf-8")
    print()
    print("RESULT:", "ALL REQUIRED CHECKS PASSED" if rep.all_required_ok else "BLOOMBERG UNAVAILABLE OR INCOMPLETE")
    print(f"Reports: {json_path}\n         {txt_path}")
    return 0 if rep.all_required_ok else 1


if __name__ == "__main__":
    sys.exit(main())
