#!/usr/bin/env python
"""Standalone Bloomberg diagnostic for the risk-monitor cash ladder.

Portable: Python 3 standard library + (optionally) blpapi. Imports nothing from the
repository, so it runs on the Bloomberg computer with only this file and the SQLite
database. READ-ONLY: it opens the database in read-only mode and never writes marks.
No mock, random or fallback prices: a ticker either returns a Bloomberg value or is
reported FAILED with the exact error text.

Usage:
    py -3 tools\\bloomberg_diagnostic.py --once
    py -3 tools\\bloomberg_diagnostic.py --once --db C:\\path\\risk.db --host localhost --port 8194

Checks (all required for exit code 0):
    1  python        version / executable
    2  blpapi        import
    3  tcp           localhost:8194 connectivity
    4  session       blpapi Session start
    5  service       //blp/refdata open
    6  database      FX instruments with open legs + their settle dates
    7  spot          PX_LAST for every open pair (ReferenceDataRequest)
    8  forward       one representative forward per pair: direct broken-date outright
                     (FWD_CURVE + SETTLE_DT override); if that field is absent, the
                     standard 1M tenor ticker '<PAIR>1M Curncy' PX_LAST is requested and
                     reported as such (never mixed up with the outright)
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
        print(f"[{'OK    ' if ok else 'FAILED'}] {name:9s} {detail}")

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
            if as_of is None:
                row = conn.execute("SELECT MAX(as_of_date) FROM positions").fetchone()
                as_of = row[0] if row and row[0] else date.today().isoformat()
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
    # ---- forward: direct broken-date outright per pair, fallback report on 1M tenor ticker
    fwd_ok = 0
    for r in reqs:
        settle = r["settle_date"]
        yyyymmdd = settle.replace("-", "")
        try:
            data, errors = reference_request(blpapi, session, service, [r["ticker"]], ["FWD_CURVE"],
                                             {"FWD_CURVE_QUOTE_FORMAT": "OUTRIGHTS", "SETTLE_DT": yyyymmdd})
            v = data.get(r["ticker"], {}).get("FWD_CURVE")
            if isinstance(v, float):
                fwd_ok += 1
                rep.ticker(status="OK", instrument_id=r["instrument_id"], mark_type="FWD_OUTRIGHT", ticker=r["ticker"],
                           settle_date=settle, value=v, request_type="ReferenceDataRequest FWD_CURVE/SETTLE_DT",
                           source="BBG_BFXFORWARD", snapped_at=snapped, detail="direct broken-date outright")
                continue
            direct_err = errors.get(r["ticker"], f"FWD_CURVE not a scalar: {type(v).__name__}")
        except Exception as exc:
            direct_err = f"request raised: {exc!r}"
        tenor = f"{r['instrument_id']}1M Curncy"
        try:
            data, errors = reference_request(blpapi, session, service, [tenor], ["PX_LAST", "SETTLE_DT"])
            v = data.get(tenor, {}).get("PX_LAST")
            if isinstance(v, float):
                fwd_ok += 1
                rep.ticker(status="OK", instrument_id=r["instrument_id"], mark_type="FWD_POINTS_1M", ticker=tenor,
                           settle_date=str(data[tenor].get("SETTLE_DT", "")), value=v,
                           request_type="ReferenceDataRequest PX_LAST (1M tenor)", source="BBG_BDP", snapped_at=snapped,
                           detail=f"direct outright unavailable ({direct_err}); 1M forward points returned instead")
                continue
            tenor_err = errors.get(tenor, "no PX_LAST in response")
        except Exception as exc:
            tenor_err = f"request raised: {exc!r}"
        rep.ticker(status="FAILED", instrument_id=r["instrument_id"], mark_type="FWD_OUTRIGHT", ticker=r["ticker"],
                   settle_date=settle, value=None, request_type="ReferenceDataRequest FWD_CURVE / 1M tenor",
                   source="", snapped_at="", detail=f"direct: {direct_err} | tenor {tenor}: {tenor_err}")
    rep.check("forward", fwd_ok == len(reqs), f"{fwd_ok}/{len(reqs)} pairs returned a forward price")


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
    p.add_argument("--as-of", default=None, help="as-of date for open legs (default: latest positions date)")
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
    if session is not None:
        try:
            session.stop()
        except Exception:
            pass

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
