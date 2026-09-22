"""The golden book: the sample blotter valued at synthetic marks, pinned to tests/golden/book.json.

This is the behaviour-neutrality proof behind the infra agent (CLAUDE.md "Infra agent"):
a refactor anywhere may change nothing here. `data/sample/blotter_sample.csv` is loaded into
an in-memory database, every mark the book can read is written with a deterministic value
(a fixed base per pair, moved by a hash of the mark's key, so the numbers are stable across
machines and Python versions), then for each as-of date in AS_OF_DATES, in order, the
ledger realises what has settled and the book is valued: every value_book row, LTD, the
period P&L, the cash ladder, the delta per currency, the per-pair delta and the spot table.

The pinned file is regenerated ONLY in a commit the user has approved (CLAUDE.md hard rule
7: a change to what the book is worth is a change to P&L arithmetic):

    python -m tests.golden_book --write

Run without --write it prints the differences from the pinned file, like the test does.

What is deliberately in the fixture: settled and open FX, a metal with spot but no forward
curve (XAUUSD: the near-marks rule's spot-only case), NDF pairs with NDF_1M and NDF_FIX
marks, futures at and after expiry, swaps, and FX options including a closed-out pair.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Dict, Iterable, List

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "sample" / "blotter_sample.csv"
GOLDEN = ROOT / "tests" / "golden" / "book.json"
AS_OF_DATES = ("2026-08-14", "2026-09-04", "2026-09-18")
STAMP = "T15:00:00-04:00"
REL_TOL, ABS_TOL = 1e-9, 1e-6
FX_PRODUCTS = ("FX_SPOT", "FX_FWD", "FX_SWAP")
METALS = {"XAUUSD", "XAGUSD"}          # spot only: no forward curve is written for them
SPOT_BASE = {
    "AUDUSD": 0.66, "EURSEK": 11.1, "EURUSD": 1.17, "GBPUSD": 1.35, "USDBRL": 5.4, "USDCAD": 1.37,
    "USDCHF": 0.80, "USDHKD": 7.8, "USDIDR": 16400.0, "USDJPY": 147.0, "USDKRW": 1390.0, "USDMXN": 18.7,
    "USDNOK": 10.0, "USDSEK": 9.4, "USDSGD": 1.28, "USDTRY": 41.0, "USDTWD": 30.3, "USDZAR": 17.5,
    "XAUUSD": 3650.0,
}
FUTURE_BASE = 6000.0


def _wobble(*key: str, width: float = 0.02) -> float:
    """A number in [-width, width) fixed by `key`: sha256, never Python's salted hash()."""
    digest = hashlib.sha256("|".join(key).encode("utf-8")).digest()
    unit = int.from_bytes(digest[:8], "big") / 2 ** 64
    return (2 * unit - 1) * width


def _mark(conn: sqlite3.Connection, as_of: str, instrument_id: str, settle_date: str, mark_type: str,
          value: float, source: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO marks (as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (as_of, instrument_id, settle_date, mark_type, float(value), source, as_of + STAMP))


def mark_dates() -> List[str]:
    """AS_OF_DATES and every reference close their period P&L reads."""
    from engine.pnl.ledger import period_reference_dates
    dates = set(AS_OF_DATES)
    for d in AS_OF_DATES:
        dates.update(period_reference_dates(d).values())
    return sorted(dates)


def build_book(conn: sqlite3.Connection = None) -> sqlite3.Connection:
    from data.ingest import blotter, schema
    from engine.ladder.ndf import is_ndf_pair

    conn = conn if conn is not None else schema.connect()
    blotter.load(SAMPLE, conn)
    pairs = [r[0] for r in conn.execute("SELECT instrument_id FROM instruments WHERE asset_class = 'FX' ORDER BY 1")]
    leg_dates: Dict[str, List[str]] = {}
    for pair, settle in conn.execute(
            "SELECT DISTINCT t.instrument_id, l.settle_date FROM trades t JOIN trade_legs l USING (trade_id) "
            f"WHERE t.product IN ({','.join('?' * len(FX_PRODUCTS))}) ORDER BY 1, 2", FX_PRODUCTS):
        leg_dates.setdefault(pair, []).append(settle)
    futures = conn.execute("SELECT instrument_id, expiry_date FROM instruments WHERE asset_class = 'FUTURE' ORDER BY 1").fetchall()
    swaps = conn.execute(
        "SELECT t.instrument_id, l.settle_date, t.quantity FROM trades t JOIN trade_legs l USING (trade_id) "
        "WHERE t.product = 'IRS' AND l.leg_no = 1 ORDER BY 1").fetchall()
    options = conn.execute(
        "SELECT t.instrument_id, l.settle_date, t.price FROM trades t JOIN trade_legs l USING (trade_id) "
        "WHERE t.product = 'FX_OPTION' ORDER BY 1").fetchall()

    with conn:
        for d in mark_dates():
            for pair in pairs:
                spot = SPOT_BASE.get(pair, 1.0) * (1 + _wobble(pair, "SPOT", d))
                _mark(conn, d, pair, d, "SPOT", spot, "BBG_BFXFORWARD")
                if pair not in METALS:
                    for settle in leg_dates.get(pair, []):
                        _mark(conn, d, pair, settle, "FWD_OUTRIGHT", spot * (1 + _wobble(pair, "FWD", settle, d, width=0.005)),
                              "BBG_BFXFORWARD")
                if is_ndf_pair(pair):
                    _mark(conn, d, pair, d, "NDF_1M", spot * (1 + _wobble(pair, "NDF_1M", d, width=0.005)), "BBG_BFXFORWARD")
                    _mark(conn, d, pair, d, "NDF_FIX", spot * (1 + _wobble(pair, "NDF_FIX", d, width=0.002)), "BBG_BDH")
            for instrument_id, expiry in futures:
                _mark(conn, d, instrument_id, expiry, "FUTURE_PX", FUTURE_BASE * (1 + _wobble(instrument_id, "PX", d)), "BBG_BDH")
            for instrument_id, maturity, quantity in swaps:
                _mark(conn, d, instrument_id, maturity, "PV_USD", quantity * 0.1 * _wobble(instrument_id, "PV", d), "QL_PRICER")
                _mark(conn, d, instrument_id, maturity, "CASHFLOW_USD",
                      quantity * 0.02 * _wobble(instrument_id, "CF", d) if d >= "2026-09-01" else 0.0, "QL_PRICER")
                _mark(conn, d, instrument_id, maturity, "DV01_USD", abs(quantity) * 0.0001 * (1 + _wobble(instrument_id, "DV01", d)), "QL_PRICER")
                _mark(conn, d, instrument_id, maturity, "PAR_RATE", 0.04 * (1 + _wobble(instrument_id, "PAR", d)), "QL_PRICER")
            for instrument_id, expiry, fill in options:
                _mark(conn, d, instrument_id, expiry, "PREMIUM", fill * (1 + _wobble(instrument_id, "PREM", d, width=0.2)), "QL_OPTIONS_PRICER")
                _mark(conn, d, instrument_id, expiry, "DELTA", 0.45 + _wobble(instrument_id, "DELTA", d, width=0.2), "QL_OPTIONS_PRICER")
    return conn


def _plain(value):
    """JSON-safe scalar: NaN -> None, numpy scalars -> Python."""
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _rows(df) -> List[dict]:
    return [{k: _plain(v) for k, v in rec.items()} for rec in df.to_dict("records")]


def snapshot(conn: sqlite3.Connection) -> dict:
    from engine.ladder import ladder
    from engine.pnl import ledger
    from engine.pnl.valuation import value_book

    out = {"sample": SAMPLE.name, "as_of_dates": list(AS_OF_DATES), "days": {}}
    for d in AS_OF_DATES:
        realised = ledger.realise_settled(conn, d)
        out["days"][d] = {
            "realised": {"realised": realised["realised"],
                         "unrealisable": sorted(u["trade_id"] for u in realised["unrealisable"]),
                         "repaired": sorted(realised["repaired"])},
            "value_book": _rows(value_book(conn, d)),
            "ltd": _plain(ledger.ltd(conn, d)),
            "period_pnl": {k: {kk: _plain(vv) for kk, vv in v.items()} for k, v in ledger.period_pnl(conn, d).items()},
            "cash_ladder": _rows(ladder.cash_ladder(conn, d)),
            "delta_per_ccy": _rows(ladder.delta_per_ccy(conn, d)),
            "per_pair_delta": _rows(ladder.per_pair_delta(conn, d)),
            "spot_table": _rows(ladder.spot_table(conn, d)),
        }
    return out


def compare(expected, actual, path: str = "") -> List[str]:
    """Every place `actual` differs from `expected`, as 'path: expected != actual'. Numbers
    compare with a tolerance (REL_TOL / ABS_TOL); everything else exactly."""
    diffs: List[str] = []
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            sub = f"{path}.{key}" if path else str(key)
            if key not in expected:
                diffs.append(f"{sub}: not in golden")
            elif key not in actual:
                diffs.append(f"{sub}: missing")
            else:
                diffs.extend(compare(expected[key], actual[key], sub))
    elif isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            diffs.append(f"{path}: {len(expected)} rows != {len(actual)} rows")
        for i, (e, a) in enumerate(zip(expected, actual)):
            diffs.extend(compare(e, a, f"{path}[{i}]"))
    elif isinstance(expected, (int, float)) and not isinstance(expected, bool) \
            and isinstance(actual, (int, float)) and not isinstance(actual, bool):
        if not math.isclose(expected, actual, rel_tol=REL_TOL, abs_tol=ABS_TOL):
            diffs.append(f"{path}: {expected!r} != {actual!r}")
    elif expected != actual:
        diffs.append(f"{path}: {expected!r} != {actual!r}")
    return diffs


def dumps(data: dict) -> str:
    """Standard JSON, laid out for diffs: dicts one key per line, a list of rows one compact
    row per line, so a re-pin shows exactly which trades moved."""
    def emit(obj, indent: int) -> str:
        pad = " " * indent
        if isinstance(obj, dict):
            if not obj:
                return "{}"
            items = [f"{pad} {json.dumps(k)}: {emit(v, indent + 1)}" for k, v in sorted(obj.items())]
            return "{\n" + ",\n".join(items) + f"\n{pad}}}"
        if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
            items = [f"{pad} " + json.dumps(x, sort_keys=True, separators=(",", ":"), allow_nan=False) for x in obj]
            return "[\n" + ",\n".join(items) + f"\n{pad}]"
        return json.dumps(obj, sort_keys=True, allow_nan=False)
    return emit(data, 0) + "\n"


def main(argv: Iterable[str] = None) -> int:
    ap = argparse.ArgumentParser(description="the golden book: compare with, or (--write) re-pin, tests/golden/book.json")
    ap.add_argument("--write", action="store_true", help="re-pin the golden file (needs the user's approval, CLAUDE.md hard rule 7)")
    args = ap.parse_args(list(argv) if argv is not None else None)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    actual = snapshot(build_book())
    if args.write:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(dumps(actual), encoding="utf-8")
        rows = sum(len(day["value_book"]) for day in actual["days"].values())
        print(f"wrote {GOLDEN.relative_to(ROOT).as_posix()}: {len(actual['days'])} days, {rows} valued rows")
        return 0
    if not GOLDEN.exists():
        print(f"{GOLDEN.relative_to(ROOT).as_posix()} does not exist; run with --write")
        return 1
    diffs = compare(json.loads(GOLDEN.read_text(encoding="utf-8")), actual)
    print("\n".join(diffs[:50]) if diffs else "matches the golden")
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
