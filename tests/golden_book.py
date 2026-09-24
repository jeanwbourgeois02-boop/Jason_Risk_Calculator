"""The golden book: the sample blotter valued at synthetic marks, pinned to tests/golden/book.json.

This is the behaviour-neutrality proof behind the infra lane (CLAUDE.md "Working mode"): a
refactor anywhere may change nothing here. `data/sample/blotter_sample.csv` (the synthetic
commodity book: futures, options on futures, LME forwards, FX hedges and FX options) is
loaded into an in-memory database, every mark the book can read is written with a
deterministic value (a fixed base per pair, contract root or metal, moved by a hash of the
mark's key, so the numbers are stable across machines and Python versions), then for each
as-of date in AS_OF_DATES, in order, the ledger realises what has settled and the book is
valued: every value_book row, LTD, the period P&L, the cash ladder, the delta per currency,
the per-pair delta, the spot table, the curve positions and the expiry schedule (the
ledger's wall-clock `frozen_at` replaced by a placeholder, so the file does not depend on
the day it is built).

The pinned file is regenerated ONLY in a commit the user has approved (CLAUDE.md hard rule
7: a change to what the book is worth is a change to P&L arithmetic):

    python -m tests.golden_book --write

Run without --write it prints the differences from the pinned file, like the test does.

What is deliberately in the fixture: commodity futures in USD, CNY, EUR, GBP and JPY (each
converted at its own USD pair's spot, USDCNY included), one of them expired and frozen
(CLQ26); open FX forwards and a cross (EURGBP), one USDCNH forward settled; an FX spot; a
metal with spot but no forward curve (XAUUSD: the near-marks rule's spot-only case); FX
options including a closed-out pair and one with no strike on file; options on futures
(CMDTY_OPTION: WTI, an expired COMEX gold call that freezes, SHFE copper in CNY) at a synthetic
Bloomberg price of their own; LME forwards (LME_FWD: copper, aluminium sold, and a nickel
prompt already past) on a synthetic LME curve, cash price and 3M / monthly pillars; and the
sample's two rejected rows, which never reach the book.

`python -m tests.golden_book --diff` is the report for a re-pin decision: the differences
per top-level key and day, and the proof that every trade pinned in the golden file values
exactly as before, with the new trades' LTD beside it (`golden_diff`, `format_diff`). It
never writes the pinned file.
"""
from __future__ import annotations

import argparse
import datetime as dt
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
FX_PRODUCTS = ("FX_SPOT", "FX_FWD")
METALS = {"XAUUSD", "XAGUSD"}          # spot only: no forward curve is written for them
SPOT_BASE = {
    "EURGBP": 0.866, "EURUSD": 1.17, "GBPUSD": 1.35, "USDCNH": 7.13, "USDCNY": 7.12, "USDJPY": 146.0,
    "XAUUSD": 3380.0,
}
# A futures price per contract root, in the root's own quoted scale (the sample's fills):
# USD/bbl for crude, cents/gal for RBOB and heating oil, cents/bu for the grains, USD/st for
# meal, cents/lb for soybean oil and copper, USD/oz for gold and silver, CNY/t for SHFE copper
# and DCE iron ore, USD/t for SGX iron ore, EUR/MWh for TTF, pence/therm for NBP, JPY/g for
# OSE gold. A root the sample gains without a line here raises KeyError: add its base.
FUTURE_BASE = {
    "NYMEX:CL": 68.5, "ICE:B": 72.0, "NYMEX:RB": 205.0, "NYMEX:HO": 238.0,
    "CBOT:ZS": 1045.0, "CBOT:ZM": 310.0, "CBOT:ZL": 52.4, "CBOT:ZC": 432.0,
    "COMEX:GC": 3400.0, "COMEX:SI": 38.5, "COMEX:HG": 455.0,
    "SHFE:CU": 78500.0, "DCE:I": 765.0, "SGX:FEF": 101.0,
    "ICE:TFM": 35.0, "ICE:M": 88.7, "OSE:JAU": 15400.0,
}
# An LME cash price per metal root, USD per tonne. A metal the sample gains without a line here
# raises KeyError: add its base.
LME_BASE = {"LME:CA": 9800.0, "LME:AH": 2620.0, "LME:NI": 15300.0}
_QUOTED_AGAINST_USD = {"AUD", "EUR", "GBP", "NZD", "XAU", "XAG"}   # '<ccy>USD'; every other is 'USD<ccy>'


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


def _usd_pair(ccy: str) -> str:
    return f"{ccy}USD" if ccy in _QUOTED_AGAINST_USD else f"USD{ccy}"


def _ensure_conversion_pairs(conn: sqlite3.Connection) -> None:
    """The USD pair of every non-USD futures currency, as the live pull would create it before
    writing its SPOT (a CNY future needs USDCNY, which no FX trade of the sample books)."""
    ccys = {r[0] for r in conn.execute(
        "SELECT DISTINCT quote_ccy FROM instruments WHERE asset_class = 'FUTURE' AND quote_ccy <> 'USD'")}
    for ccy in sorted(ccys):
        pair = _usd_pair(ccy)
        base, quote = (ccy, "USD") if pair.startswith(ccy) else ("USD", ccy)
        conn.execute(
            "INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
            "bbg_ticker, expiry_date) VALUES (?, 'FX', ?, ?, 1, 0, ?, '9999-12-31')",
            (pair, base, quote, f"{pair} Curncy"))


def build_book(conn: sqlite3.Connection = None) -> sqlite3.Connection:
    from data.ingest import blotter, schema

    conn = conn if conn is not None else schema.connect()
    blotter.load(SAMPLE, conn)
    with conn:
        _ensure_conversion_pairs(conn)
    pairs = [r[0] for r in conn.execute("SELECT instrument_id FROM instruments WHERE asset_class = 'FX' ORDER BY 1")]
    curve_dates: Dict[str, set] = {}
    for pair, settle in conn.execute(
            "SELECT DISTINCT t.instrument_id, l.settle_date FROM trades t JOIN trade_legs l USING (trade_id) "
            f"WHERE t.product IN ({','.join('?' * len(FX_PRODUCTS))})", FX_PRODUCTS):
        curve_dates.setdefault(pair, set()).add(settle)
    for base, quote, expiry in conn.execute(
            "SELECT i.base_ccy, i.quote_ccy, i.expiry_date FROM trades t JOIN instruments i USING (instrument_id) "
            "WHERE t.product = 'FX_OPTION'"):
        curve_dates.setdefault(base + quote, set()).add(expiry)
    futures = conn.execute(
        "SELECT instrument_id, base_ccy, expiry_date FROM instruments WHERE asset_class = 'FUTURE' ORDER BY 1").fetchall()
    options = conn.execute(
        "SELECT t.instrument_id, l.settle_date, t.price FROM trades t JOIN trade_legs l USING (trade_id) "
        "WHERE t.product = 'FX_OPTION' ORDER BY 1").fetchall()

    with conn:
        for d in mark_dates():
            for pair in pairs:
                spot = SPOT_BASE[pair] * (1 + _wobble(pair, "SPOT", d))
                _mark(conn, d, pair, d, "SPOT", spot, "BBG_BFXFORWARD")
                if pair not in METALS:
                    for settle in sorted(curve_dates.get(pair, ())):
                        _mark(conn, d, pair, settle, "FWD_OUTRIGHT", spot * (1 + _wobble(pair, "FWD", settle, d, width=0.005)),
                              "BBG_BFXFORWARD")
            for instrument_id, root_id, expiry in futures:
                _mark(conn, d, instrument_id, expiry, "FUTURE_PX",
                      FUTURE_BASE[root_id] * (1 + _wobble(instrument_id, "PX", d)), "BBG_BDH")
            for instrument_id, expiry, fill in options:
                _mark(conn, d, instrument_id, expiry, "PREMIUM", fill * (1 + _wobble(instrument_id, "PREM", d, width=0.2)), "QL_OPTIONS_PRICER")
                _mark(conn, d, instrument_id, expiry, "DELTA", 0.45 + _wobble(instrument_id, "DELTA", d, width=0.2), "QL_OPTIONS_PRICER")
    _mark_listed_options(conn)
    _mark_lme_curves(conn)
    return conn


def _mark_listed_options(conn: sqlite3.Connection) -> None:
    """Bloomberg's own price of each option on a future (official FUTURE_PX, BBG_BDH, keyed on
    the option's expiry) on every mark date, around its fill; an option that expired before the
    last as-of date is also marked on its expiry day, on its own instrument only, so the ledger
    has a price on or before expiry to freeze at. No Greeks: the P&L needs none."""
    options = conn.execute(
        "SELECT i.instrument_id, i.expiry_date, MIN(t.price) FROM trades t JOIN instruments i USING (instrument_id) "
        "WHERE t.product = 'CMDTY_OPTION' GROUP BY i.instrument_id ORDER BY 1").fetchall()
    with conn:
        for instrument_id, expiry, fill in options:
            days = set(mark_dates())
            if expiry <= AS_OF_DATES[-1]:
                days.add(expiry)
            for d in sorted(days):
                _mark(conn, d, instrument_id, expiry, "FUTURE_PX",
                      fill * (1 + _wobble(instrument_id, "PX", d, width=0.2)), "BBG_BDH")


def _mark_lme_curves(conn: sqlite3.Connection) -> None:
    """Each LME metal's curve on every mark date, on the pillars `engine.lme.lme_curve_tickers`
    gives that day: the cash price (SPOT, settle = as-of), the 3M prompt, and the monthly
    prompts up to the first one on or after the metal's last ticket prompt (FWD_OUTRIGHT), all
    BBG_BFXFORWARD, each pillar a small move off that day's cash price."""
    from engine.lme import lme_curve_tickers

    last_prompt = dict(conn.execute(
        "SELECT t.instrument_id, MAX(l.settle_date) FROM trades t JOIN trade_legs l USING (trade_id) "
        "WHERE t.product = 'LME_FWD' GROUP BY t.instrument_id ORDER BY 1").fetchall())
    with conn:
        for root_id in sorted(last_prompt):
            for d in mark_dates():
                cash = LME_BASE[root_id] * (1 + _wobble(root_id, "SPOT", d))
                _mark(conn, d, root_id, d, "SPOT", cash, "BBG_BFXFORWARD")
                pillars = [p for p in lme_curve_tickers(root_id, d) if p["mark_type"] == "FWD_OUTRIGHT"]
                beyond = [p["settle_date"] for p in pillars if p["settle_date"] >= last_prompt[root_id]]
                reach = min(beyond) if beyond else max(p["settle_date"] for p in pillars)
                for p in pillars:
                    if p["kind"] == "3M" or p["settle_date"] <= reach:
                        _mark(conn, d, root_id, p["settle_date"], "FWD_OUTRIGHT",
                              cash * (1 + _wobble(root_id, "FWD", p["settle_date"], d, width=0.005)), "BBG_BFXFORWARD")


def _plain(value):
    """JSON-safe value, recursively: NaN -> None, numpy scalars -> Python, dates -> ISO,
    tuples and sets -> lists (a set sorted)."""
    if hasattr(value, "item") and not isinstance(value, (list, tuple, dict)):
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _rows(df) -> List[dict]:
    return [{k: _plain(v) for k, v in rec.items()} for rec in df.to_dict("records")]


def _without_wall_clock(schedule: dict) -> dict:
    """The expiry schedule with the ledger's `frozen_at` (the wall-clock time the fixture ran)
    replaced by a placeholder, in its own field and in the reason that quotes its date, so the
    pinned file does not change with the day it is built."""
    for entry in schedule.get("settled_expired") or []:
        stamp = entry.get("frozen_at")
        if stamp:
            entry["frozen_at"] = "<frozen_at>"
            if isinstance(entry.get("reason"), str):
                entry["reason"] = entry["reason"].replace(f" (on {str(stamp)[:10]})", " (on <frozen_at>)")
    return schedule


def snapshot(conn: sqlite3.Connection) -> dict:
    from engine.curve import curve_positions
    from engine.expiry import expiry_schedule
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
            "curve_positions": _plain(curve_positions(conn, d)),
            "expiry_schedule": _without_wall_clock(_plain(expiry_schedule(conn, d))),
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


def _book_rows(snap: dict) -> Dict[str, Dict[str, dict]]:
    """{as-of date: {trade_id: value_book row}} of a snapshot."""
    return {d: {str(r["trade_id"]): r for r in day.get("value_book", [])} for d, day in snap.get("days", {}).items()}


def golden_diff(expected: dict, actual: dict) -> dict:
    """What re-pinning `expected` (the golden file) to `actual` (a fresh snapshot) would change.

    - counts: {day: {top-level key: number of differences}} ('*' for the keys outside the days);
    - changed: [(trade_id, day, [differences])] for every trade pinned in `expected` whose
      value_book row is not identical on a pinned day (a missing row included): the proof is
      that this list is empty;
    - pinned: how many trades the golden file pins, and on how many (trade, day) pairs;
    - new: [{trade_id, product, instrument_id, ltd: {day: pnl_usd}, status}] for the trades in
      `actual` that `expected` does not pin."""
    counts: Dict[str, Dict[str, int]] = {}
    outside = {k for k in set(expected) | set(actual) if k != "days"}
    for key in sorted(outside):
        n = len(compare({key: expected.get(key)}, {key: actual.get(key)}))
        if n:
            counts.setdefault("*", {})[key] = n
    e_days, a_days = expected.get("days", {}), actual.get("days", {})
    for d in sorted(set(e_days) | set(a_days)):
        e_day, a_day = e_days.get(d, {}), a_days.get(d, {})
        for key in sorted(set(e_day) | set(a_day)):
            if key not in e_day or key not in a_day:
                n = 1
            else:
                n = len(compare(e_day[key], a_day[key]))
            if n:
                counts.setdefault(d, {})[key] = n

    e_rows, a_rows = _book_rows(expected), _book_rows(actual)
    changed: List[tuple] = []
    pinned_ids, pairs = set(), 0
    for d in sorted(e_rows):
        for trade_id in sorted(e_rows[d]):
            pinned_ids.add(trade_id)
            pairs += 1
            row = a_rows.get(d, {}).get(trade_id)
            if row is None:
                changed.append((trade_id, d, ["row missing from the new book"]))
                continue
            diffs = compare(e_rows[d][trade_id], row)
            if diffs:
                changed.append((trade_id, d, diffs))

    new: Dict[str, dict] = {}
    for d in sorted(a_rows):
        for trade_id, row in sorted(a_rows[d].items()):
            if trade_id in pinned_ids:
                continue
            entry = new.setdefault(trade_id, {"trade_id": trade_id, "product": row.get("product"),
                                              "instrument_id": row.get("instrument_id"), "ltd": {}, "status": {}})
            entry["ltd"][d] = row.get("pnl_usd")
            entry["status"][d] = row.get("status")
    return {"counts": counts, "changed": changed, "pinned": {"trades": len(pinned_ids), "rows": pairs},
            "new": [new[k] for k in sorted(new)]}


def format_diff(result: dict) -> str:
    """The plain-text report of `golden_diff`: the summary, then the proof, then the new trades."""
    lines = ["GOLDEN BOOK DIFF: a fresh snapshot against tests/golden/book.json (nothing is written)", "",
             "Summary: differences per top-level key, per day"]
    if not result["counts"]:
        lines.append("  none: the fresh snapshot matches the golden")
    for d, keys in result["counts"].items():
        lines.append(f"  {d}: " + ", ".join(f"{k} {n}" for k, n in keys.items()))
    lines.append(f"  total: {sum(sum(k.values()) for k in result['counts'].values())} "
                 "(lists compare by position: a row added in the middle shifts every row after it)")
    lines += ["", f"Proof: the {result['pinned']['trades']} trades pinned in the golden file, "
                  f"on {result['pinned']['rows']} pinned (trade, day) rows"]
    if not result["changed"]:
        lines.append("  every pinned trade's value_book row is identical on every pinned date")
    else:
        lines.append(f"  {len(result['changed'])} pinned row(s) differ:")
        for trade_id, d, diffs in result["changed"]:
            lines.append(f"  {trade_id} on {d}:")
            lines += [f"    {x}" for x in diffs]
    lines += ["", f"New trades (in the new book, not pinned in the golden file): {len(result['new'])}"]
    for t in result["new"]:
        ltd = ", ".join(f"{d} {'n/a' if v is None else f'{v:,.2f}'} ({t['status'][d]})" for d, v in t["ltd"].items())
        lines.append(f"  {t['trade_id']} {t['product']} {t['instrument_id']}: LTD USD {ltd}")
    return "\n".join(lines) + "\n"


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
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="re-pin the golden file (needs the user's approval, CLAUDE.md hard rule 7)")
    mode.add_argument("--diff", action="store_true",
                      help="report what a re-pin would change and prove the pinned trades unchanged "
                           "(also saved under reports/); never writes the golden file")
    args = ap.parse_args(list(argv) if argv is not None else None)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    actual = snapshot(build_book())
    if args.diff:
        if not GOLDEN.exists():
            print(f"{GOLDEN.relative_to(ROOT).as_posix()} does not exist: nothing to compare with")
            return 1
        result = golden_diff(json.loads(GOLDEN.read_text(encoding="utf-8")), actual)
        text = format_diff(result)
        out = ROOT / "reports" / f"golden_diff_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")
        print(text + f"saved to {out.relative_to(ROOT).as_posix()}")
        return 1 if result["changed"] else 0
    if args.write:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(dumps(actual), encoding="utf-8", newline="\n")
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
