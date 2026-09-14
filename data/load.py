"""Command-line loader: BNP CSV (+ optional canonical marks CSV) -> data/raw/risk.db.

Usage:
    py -3 -m data.load data/raw/HA_PNL_20260818.csv
    py -3 -m data.load data/raw/HA_PNL_20260818.csv --db path/to/risk.db
    py -3 -m data.load data/raw/HA_PNL_20260818.csv --marks path/to/marks.csv
    py -3 -m data.load data/raw/HA_PNL_20260818.csv --allow-rejects
    py -3 -m data.load data/raw/HA_PNL_20260818.csv --allow-conflicts

Loads BNP trades/legs/positions via data.ingest.bnp.load (on_duplicate='skip', so a
second run on the same file inserts nothing new rather than raising IntegrityError) and
BNP_BVAL marks via data.bloomberg.bnp_marks.load_bnp_marks. Prints one summary line and
exits non-zero if there were any genuine rejects (parse/validation failures) or
conflicts (see below), unless --allow-rejects / --allow-conflicts is given. Rows skipped
only because their primary key already exists in the DB with the SAME value (idempotent
re-run of the same file) are never counted as rejects or conflicts.

A CONFLICT is a row whose primary key already exists in the DB but whose value columns
differ (e.g. BNP re-issues a file with a corrected Local Cost / Price for an existing
trade_id, or a mark value changes for the same (as_of, instrument, settle, type,
source)). The existing DB row is always kept; the amended row is reported on stderr
with the differing column(s) and never silently applied. --allow-rejects does NOT
suppress the conflicts exit code -- use --allow-conflicts for that, since a conflict is
a distinct signal (stale/amended source data) from a parse failure.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from data.bloomberg.bnp_marks import extract_bnp_marks
from data.bloomberg.marks_csv import load_mark_rows, load_marks_csv
from data.ingest.bnp import load as load_bnp
from data.ingest.schema import connect

DEFAULT_DB = Path("data/raw/risk.db")


def _values_close(a, b) -> bool:
    """Small abs/relative float tolerance so re-parsing an identical file never flags a
    conflict; see data/ingest/bnp.py's _values_match for the trades/legs/positions twin."""
    try:
        af, bf = float(a), float(b)
    except (TypeError, ValueError):
        return a == b
    return abs(af - bf) <= max(1e-6, 1e-6 * max(abs(af), abs(bf)))


def _load_bnp_marks_idempotent(csv_path, conn, as_of_date=None):
    """Load BNP_BVAL marks, distinguishing duplicate-key skips, conflicts and genuine
    rejects.

    Genuine rejects = row-level parse rejects from extract_bnp_marks (bad symbol/
    description, conflicting FWD_OUTRIGHT/SPOT values) plus any reject load_mark_rows
    still raises after duplicates are pre-filtered (e.g. unknown instrument_id, bad
    mark_type/source/date, non-finite value). Rows whose primary key already exists in
    ``marks`` with the SAME value (within tolerance) are 'skipped'; rows whose key
    exists but whose value differs are a CONFLICT -- the existing DB value is kept and
    the row is reported, never silently applied.

    Returns (n_loaded, n_skipped, n_conflicts, reject_details, conflict_details).
    """
    result = extract_bnp_marks(csv_path, as_of_date)
    reject_details = [f"row {rj.row_no} {rj.symbol}: {rj.reason}" for rj in result.rejects]

    existing = {
        (r[0], r[1], r[2], r[3], r[4]): r[5] for r in conn.execute(
            "SELECT as_of_date, instrument_id, settle_date, mark_type, source, value FROM marks")
    }
    new_rows = []
    n_skipped = 0
    n_conflicts = 0
    conflict_details = []
    for row in result.rows:
        key = (row.as_of_date, row.instrument_id, row.settle_date, row.mark_type, row.source)
        if key in existing:
            if _values_close(row.value, existing[key]):
                n_skipped += 1
            else:
                n_conflicts += 1
                conflict_details.append(
                    f"mark {key}: value {row.value} (file) != {existing[key]} (DB)")
        else:
            new_rows.append(row)
            existing[key] = row.value

    load_result = load_mark_rows(new_rows, conn, strict=False, name=Path(csv_path).name)
    reject_details += [f"row {rj.row_no}: {rj.detail}" for rj in load_result.rejects]

    return load_result.n_loaded, n_skipped, n_conflicts, reject_details, conflict_details


def _load_marks_csv_idempotent(path, conn):
    """Load a canonical marks CSV, pre-filtering rows whose primary key already exists
    in ``marks`` so a second run counts them as 'skipped' (same value, within tolerance)
    or a CONFLICT (key exists but value differs -- the existing DB value is kept and the
    row is reported, never silently applied) rather than rejecting them as duplicates.
    Genuine rejects (bad mark_type/source/instrument/date/value/snapped_at) are still
    reported.
    """
    import csv as csv_mod

    existing = {
        (r[0], r[1], r[2], r[3], r[4]): r[5] for r in conn.execute(
            "SELECT as_of_date, instrument_id, settle_date, mark_type, source, value FROM marks")
    }

    with Path(path).open(newline="", encoding="utf-8") as f:
        reader = csv_mod.DictReader(f)
        rows_raw = list(reader)

    # Split rows into "already present" (skip / conflict) vs "everything else"
    # (validated below by load_marks_csv itself), by checking the raw key/value columns
    # before any type validation.
    n_skipped = 0
    n_conflicts = 0
    conflict_details = []
    remaining = []
    for raw in rows_raw:
        key = (raw.get("as_of_date"), raw.get("instrument_id"), raw.get("settle_date"),
               raw.get("mark_type"), raw.get("source"))
        if key in existing and all(k is not None for k in key):
            try:
                same = _values_close(raw.get("value"), existing[key])
            except (TypeError, ValueError):
                same = False
            if same:
                n_skipped += 1
            else:
                n_conflicts += 1
                conflict_details.append(
                    f"mark {key}: value {raw.get('value')} (file) != {existing[key]} (DB)")
        else:
            remaining.append(raw)

    if not remaining:
        return 0, n_skipped, n_conflicts, [], conflict_details

    tmp_path = Path(path).with_suffix(".tmp_filtered.csv")
    import csv as csv_mod2
    with tmp_path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = reader.fieldnames
        w = csv_mod2.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for raw in remaining:
            w.writerow(raw)
    try:
        load_result = load_marks_csv(tmp_path, conn, strict=False)
    finally:
        tmp_path.unlink(missing_ok=True)

    reject_details = [f"row {rj.row_no}: {rj.detail}" for rj in load_result.rejects]
    return load_result.n_loaded, n_skipped, n_conflicts, reject_details, conflict_details


def run(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="data.load", description="Load a BNP PB CSV (and optional marks CSV) into risk.db")
    parser.add_argument("csv_path", help="path to HA_PNL_YYYYMMDD.csv")
    parser.add_argument("--db", default=str(DEFAULT_DB), help=f"SQLite DB path (default {DEFAULT_DB})")
    parser.add_argument("--as-of", dest="as_of_date", default=None,
                        help="override as_of_date (ISO); default = previous weekday of the filename date")
    parser.add_argument("--marks", default=None, help="optional canonical marks CSV to also load")
    parser.add_argument("--allow-rejects", action="store_true",
                        help="exit 0 even if there were genuine rejects")
    parser.add_argument("--allow-conflicts", action="store_true",
                        help="exit 0 even if there were conflicts (key exists, value differs)")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)

    reject_details: List[str] = []
    conflict_details: List[str] = []

    bnp_res = load_bnp(args.csv_path, conn, as_of_date=args.as_of_date, strict=False, on_duplicate="skip")
    reject_details += [f"row {rj.row_no} {rj.symbol}: {rj.reason}" for rj in bnp_res.rejects]
    reject_details += [
        f"row {f.row_no} {f.symbol} {f.check}: deviation={f.deviation} tol={f.tolerance}"
        for f in bnp_res.recon.failures
    ]
    conflict_details += bnp_res.conflict_details
    n_trades_loaded = len(bnp_res.trades) - bnp_res.skipped["trades"] - bnp_res.conflicts["trades"]
    n_trades_skipped = bnp_res.skipped["trades"]
    # Mirrors n_trades_skipped: the printed total counts trades-table conflicts (one per
    # amended trade_id); leg/position conflicts for the same trade_id are still detailed
    # on stderr via conflict_details but are not double-counted in the headline total.
    n_trades_conflicts = bnp_res.conflicts["trades"]

    n_marks_loaded, n_marks_skipped, n_marks_conflicts, marks_rejects, marks_conflict_details = (
        _load_bnp_marks_idempotent(args.csv_path, conn, as_of_date=args.as_of_date))
    reject_details += marks_rejects
    conflict_details += marks_conflict_details

    n_extra_marks_loaded = 0
    n_extra_marks_skipped = 0
    n_extra_marks_conflicts = 0
    if args.marks:
        (n_extra_marks_loaded, n_extra_marks_skipped, n_extra_marks_conflicts,
         extra_rejects, extra_conflict_details) = _load_marks_csv_idempotent(args.marks, conn)
        reject_details += extra_rejects
        conflict_details += extra_conflict_details

    total_marks_loaded = n_marks_loaded + n_extra_marks_loaded
    total_marks_skipped = n_marks_skipped + n_extra_marks_skipped
    total_marks_conflicts = n_marks_conflicts + n_extra_marks_conflicts
    n_rejects = len(reject_details)
    n_skipped = n_trades_skipped + total_marks_skipped
    n_conflicts = n_trades_conflicts + total_marks_conflicts

    print(
        f"as_of_date={bnp_res.as_of_date} trades_loaded={n_trades_loaded} "
        f"marks_loaded={total_marks_loaded} rejects={n_rejects} skipped={n_skipped} "
        f"conflicts={n_conflicts}"
    )
    for detail in reject_details[:20]:
        print(f"  reject: {detail}", file=sys.stderr)
    for detail in conflict_details[:20]:
        print(f"  conflict: {detail}", file=sys.stderr)

    conn.close()

    if n_rejects and not args.allow_rejects:
        return 1
    if n_conflicts and not args.allow_conflicts:
        return 1
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
