"""Market-data snapshot: carry the marks on file from the Bloomberg PC to a PC with no
Terminal, through git.

The database (`data/raw/risk.db`) is never committed, so a PC without Bloomberg has no
marks and every USD figure is blank. `export_snapshot` writes the market-data tables as
plain CSV under `data/bbg_snapshot/` (tracked by git, one file per table, rows in
primary-key order so a re-export diffs small; `marks` one file per month of `as_of_date`,
`marks/<YYYY-MM>.csv`, since 2026-09-30, the months in name order giving the rows of the old
single `marks.csv` in the same order); `import_snapshot` reads them back on the other PC,
either layout.

Cost (2026-09-30, the pull was slow and grew with history): each file's content is
fingerprinted by one aggregate query (row count, rowids and per-column sums), recorded with
the file's size, mtime and hash in a sidecar next to the database (`<db>.snapshot_state.json`,
this PC's own record, never committed). A file whose fingerprint and on-disk stat match the
record is neither read nor rewritten, so a pull rewrites the current month of marks and
whatever else it changed. The read transaction holds only the fingerprints and the rows of
the files that changed; the CSV is built and written after it is released. Every "Pull Bloomberg now" ends with `save_after_pull` (user, 2026-09-22: "I
dont want to need to run step 3 export, just set it up so every pull from bbg triggers the
saving", then "dont need to trigger commit and push, just need to make sure the bbg data is
logged and stored locally, I will trigger the commit and push myself"): the export alone,
so the files sit in the working copy for the user's own commit and push; a failure is
logged and never fails the pull. `2_launcher.py marks-export` is the export by hand, with a
commit of that folder alone (and `--push`) for whoever wants it; `marks-import` is the
other PC's side. Nothing here asks Bloomberg anything (hard rule 8).

What travels: `marks` whole, every source, exactly as Bloomberg and the app's own pricers
wrote it on the Bloomberg PC (values, sources and `snapped_at` untouched, so
`marks_official` decides the official row here the way it does there), plus every other
table a pull writes (MARKET_TABLES: the OIS curves and their quotes, the FX vol quotes,
Bloomberg's commodity contract dates), the `instruments` rows those marks hang off,
only so a mark's foreign key holds before the blotter is uploaded here, each table's DDL
(so a table this database has never created still lands), and the pull's own log, the
status JSON the live feed writes next to the database (`live.status_path`), copied as
pull_status.json for reading, never installed as this PC's status. No trade travels
(CLAUDE.md hard rule 1): the book on each PC is still the blotter uploaded on it.

An import makes the market data here what the Bloomberg PC had at the export: rows of any
source but MANUAL are dropped first, because a past-day row the backfill has since
replaced on the Bloomberg PC (a last live press replaced by the 15:00 close, under another
source) would otherwise stay on and win. A MANUAL row typed here is kept, and an
instrument already on file here is never overwritten. It then freezes the settled trades
the way the backfill's closing step does (`engine.pnl.ledger.realise_settled`), since no
pull ever runs here to do it. Just before that freeze it writes Bloomberg's contract dates
onto this PC's own commodity futures (`data.ingest.contract_dates.apply_contract_dates`,
2026-09-24), as a pull does on the Bloomberg PC: an upload here books each future at
contract-master's estimated expiry, and the freeze must see Bloomberg's date.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Union

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_DIR = REPO_ROOT / "data" / "bbg_snapshot"
SNAPSHOT_REL = "data/bbg_snapshot"
MANIFEST = "snapshot.json"

# Import order: instruments first (marks.instrument_id references it). Every table a
# Bloomberg pull writes (data/bloomberg/live.py and the modules it calls), each with a
# `source` column so the MANUAL rule below applies to all of them alike; a table the
# source database has not created yet is simply not in the snapshot.
INSTRUMENTS = "instruments"
MARKET_TABLES = ("marks", "curves", "curve_quotes", "vol_quotes", "contract_static")
# Left out since 2026-09-24 (commodity conversion Phase 2, user yes: the macro trader's
# products leave the app): index_fixings (swap fixings), rate_vol_quotes (swaption and cap
# vols) and equity_dividend_yields (SPX). An older snapshot that still carries them is read
# without them: their CSV files and DDL are ignored, and this PC's own rows in those tables,
# if it has the tables at all, are left as they are. curve_quotes and curves stay: the OIS
# curves still discount options.
# contract_static (2026-09-24, commodity conversion): Bloomberg's FUT_LAST_TRADE_DT /
# FUT_NOTICE_FIRST per commodity futures contract, written by the pull through
# data.contracts.store_static_dates; created on the importing PC with contract-master's own
# ensure_static_table, so its DDL is that lane's and not a copy.
CONTRACT_STATIC = "contract_static"
KEPT_SOURCE = "MANUAL"
PULL_STATUS = "pull_status.json"
# marks by month of as_of_date (2026-09-30): only the months a pull touched are rewritten.
# LEGACY_MARKS is the single file of every snapshot before; the import still reads it, and
# an export in the new layout removes it.
MARKS = "marks"
MARKS_DIR = "marks"
LEGACY_MARKS = "marks.csv"
_MONTH = re.compile(r"\d{4}-\d{2}")
STATE_SUFFIX = ".snapshot_state.json"
STATE_VERSION = 1


class SnapshotError(Exception):
    """Nothing to export, or no snapshot to import: said in plain words by the launcher."""


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_info(conn: sqlite3.Connection, table: str) -> List[tuple]:
    """PRAGMA table_info rows: (cid, name, type, notnull, dflt_value, pk); [] if absent."""
    return conn.execute(f"PRAGMA table_info({_q(table)})").fetchall()


class _File:
    """One CSV file of the snapshot: its name under the folder, the content's fingerprint,
    its row count, and (only when the fingerprint or the file on disk no longer match the
    record) its columns and rows, read inside the export's one read transaction."""

    __slots__ = ("name", "fp", "count", "cols", "rows")

    def __init__(self, name: str, fp: list, count: int, cols: Optional[List[str]] = None,
                 rows: Optional[List[tuple]] = None):
        self.name, self.fp, self.count, self.cols, self.rows = name, fp, count, cols, rows


def _columns(info: List[tuple]) -> tuple:
    """(columns in table order, primary key in key order, or every column without one)."""
    cols = [c[1] for c in info]
    key = [c[1] for c in sorted((c for c in info if c[5]), key=lambda c: c[5])] or cols
    return cols, key


_NUMERIC_DECL = ("INT", "REAL", "FLOA", "DOUB", "NUM")


def _aggregates(conn: sqlite3.Connection, table: str, info: List[tuple]) -> str:
    """The fingerprint's aggregate columns, one scan in SQLite with no row reaching Python:
    the row count, the rowids (INSERT OR REPLACE gives a replaced row a new one, and a
    deleted row takes its rowid with it), and per column the total of a number, or for text
    its total length and, for a date or time column (its name ends in 'date' or '_at'), the
    total of its julian day (a moved settle date, a new snap time or source shows). A write the app makes to these tables moves at least one of them; the
    only change they could miss is a number moving by less than the rounding of its total (a
    day's, for marks: about 1e-9 on a day of prices) on a row that also kept its rowid and its
    snap time."""
    parts = ["COUNT(*)"]
    if "WITHOUT ROWID" not in _ddl(conn, table).upper():
        parts += ["SUM(rowid)", "MAX(rowid)"]
    for c in info:
        name = _q(c[1])
        if any(t in (c[2] or "").upper() for t in _NUMERIC_DECL):
            parts.append(f"TOTAL({name})")
        else:
            parts.append(f"TOTAL(length({name}))")
            if c[1].lower().endswith(("date", "_at")):
                parts.append(f"TOTAL(julianday({name}))")
    return ", ".join(parts)


def _same_fp(fp: list, recorded: Optional[dict]) -> bool:
    return bool(recorded) and json.loads(json.dumps(fp)) == recorded.get("fp")


def _stat_matches(recorded: Optional[dict], path: Path) -> bool:
    """The file on disk is the one this PC last wrote or checked (size and mtime as recorded):
    a git checkout, an edit or a deletion since makes it unknown again."""
    if not recorded:
        return False
    try:
        st = path.stat()
    except OSError:
        return False
    return st.st_size == recorded.get("size") and st.st_mtime_ns == recorded.get("mtime_ns")


def _plan(conn: sqlite3.Connection, recorded: dict, out_dir: Path, name: str, table: str, cols: List[str],
          key: List[str], fp_row: tuple, where: str = "", params: tuple = ()) -> _File:
    """The file `name`: skipped (rows None) when its fingerprint (the columns and `fp_row`,
    whose first item is the row count) and the file on disk match the record, else its rows
    read now, in primary-key order."""
    fp = [cols] + list(fp_row)
    rec = recorded.get(name)
    if _same_fp(fp, rec) and _stat_matches(rec, out_dir / name):
        return _File(name, fp, int(fp_row[0]))
    rows = conn.execute(f"SELECT {', '.join(map(_q, cols))} FROM {_q(table)} {where} "
                        f"ORDER BY {', '.join(map(_q, key))}", params).fetchall()
    return _File(name, fp, len(rows), cols, rows)


def _month_file(month: str) -> str:
    # an as_of_date that is not ISO still gets a file of its own, named safely
    return f"{MARKS_DIR}/{month if _MONTH.fullmatch(month) else 'other-' + month.encode('utf-8').hex()}.csv"


def _marks_plan(conn: sqlite3.Connection, recorded: dict, out_dir: Path, info: List[tuple]) -> tuple:
    """marks, one file per month of as_of_date: one fingerprint query grouped by day (it walks
    the primary key, whose first column is as_of_date, so SQLite sorts nothing), a month's
    fingerprint being its days' rows; then the rows of the months that changed only (a range
    on as_of_date). Returns (the files, the first and the last as_of_date)."""
    cols, key = _columns(info)
    months: Dict[str, List[list]] = {}
    first = last = None
    for row in conn.execute(f"SELECT as_of_date, {_aggregates(conn, MARKS, info)} "
                            f"FROM marks GROUP BY as_of_date ORDER BY as_of_date"):
        months.setdefault(str(row[0])[:7], []).append(list(row))
        first, last = first if first is not None else row[0], row[0]
    files = []
    for month, days in months.items():
        if _MONTH.fullmatch(month):
            where, params = "WHERE as_of_date >= ? AND as_of_date < ?", (month, month[:-1] + chr(ord(month[-1]) + 1))
        else:
            where, params = "WHERE substr(as_of_date, 1, 7) = ?", (month,)
        count = sum(int(d[1]) for d in days)
        files.append(_plan(conn, recorded, out_dir, _month_file(month), MARKS, cols, key, (count, days), where, params))
    return files, first, last


def _csv_bytes(cols: List[str], rows: List[tuple]) -> bytes:
    # lineterminator '\n' on every OS: the same book must give the same bytes from Windows
    # and the Mac, or each export would rewrite every line. csv writes a float with repr(),
    # which reads back to the identical float.
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _state_path(db_path: Path) -> Path:
    return db_path.with_name(db_path.name + STATE_SUFFIX)


def _load_state(db_path: Path, out_dir: Path) -> dict:
    """{file name: {'fp', 'sha1', 'size', 'mtime_ns'}} last recorded for `out_dir`; {} when
    there is none or it cannot be read (every file is then checked again)."""
    try:
        state = json.loads(_state_path(db_path).read_text(encoding="utf-8"))
        if state.get("version") != STATE_VERSION:
            return {}
        return dict(state.get("dirs", {}).get(str(out_dir.resolve()), {}))
    except (OSError, ValueError, AttributeError):
        return {}


def _save_state(db_path: Path, out_dir: Path, files: dict) -> None:
    """Never fails the export: without the record the next export checks every file again."""
    path = _state_path(db_path)
    try:
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(state, dict) or state.get("version") != STATE_VERSION:
                state = {}
        except (OSError, ValueError):
            state = {}
        state = {"version": STATE_VERSION, "dirs": {**(state.get("dirs") or {}), str(out_dir.resolve()): files}}
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _write_files(out_dir: Path, plan: List[_File], recorded: dict) -> tuple:
    """Write the files of `plan` that changed; remove month files no longer in the book and
    an older snapshot's single marks.csv. Returns (names whose bytes changed or that were
    removed, the new record)."""
    changed: List[str] = []
    files: Dict[str, dict] = {}
    for f in plan:
        path = out_dir / f.name
        rec = recorded.get(f.name)
        if f.rows is None:  # unchanged since the record, file untouched on disk
            files[f.name] = rec
            continue
        data = _csv_bytes(f.cols, f.rows)
        digest = hashlib.sha1(data).hexdigest()
        if _stat_matches(rec, path):
            same = rec.get("sha1") == digest  # the file is the one recorded: no need to read it
        else:
            try:
                same = path.read_bytes() == data
            except OSError:
                same = False
        if not same:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
            changed.append(f.name)
        st = path.stat()
        files[f.name] = {"fp": json.loads(json.dumps(f.fp)), "sha1": digest, "size": st.st_size,
                         "mtime_ns": st.st_mtime_ns}
    keep = {f.name for f in plan}
    stale = [p for p in (out_dir / MARKS_DIR).glob("*.csv") if f"{MARKS_DIR}/{p.name}" not in keep]
    if any(f.name.startswith(MARKS_DIR + "/") for f in plan):
        stale.append(out_dir / LEGACY_MARKS)
    for p in stale:
        if p.exists():
            p.unlink()
            changed.append(p.relative_to(out_dir).as_posix())
    return changed, files


def _ddl(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    return str(row[0]) if row and row[0] else ""


def _copy_pull_status(db_path: Path, out_dir: Path) -> tuple:
    """(summary of the last pull for the manifest, whether pull_status.json changed)."""
    try:
        from data.bloomberg.live import status_path
        src = status_path(db_path)
    except ImportError:
        return None, False
    dst = out_dir / PULL_STATUS
    if not src.exists():
        return None, False
    data = src.read_bytes()
    changed = not dst.exists() or dst.read_bytes() != data
    if changed:
        dst.write_bytes(data)
    try:
        status = json.loads(data.decode("utf-8"))
        return {k: status.get(k) for k in ("time", "connected", "requested", "written", "failed")}, changed
    except (ValueError, AttributeError):
        return None, changed


def export_snapshot(db_path: Union[str, Path], out_dir: Union[str, Path] = SNAPSHOT_DIR) -> dict:
    """Write the snapshot of `db_path` to `out_dir`. Returns the manifest (also written as
    snapshot.json): exported_at, the first and last as_of_date in marks, rows per table,
    each table's DDL, and the last pull's summary line. Raises SnapshotError, touching
    nothing, when there is no database or no mark in it."""
    return _export(db_path, out_dir)[0]


def _export(db_path: Union[str, Path], out_dir: Union[str, Path] = SNAPSHOT_DIR) -> tuple:
    """export_snapshot's work: (manifest, names of the files whose bytes changed)."""
    db_path, out_dir = Path(db_path), Path(out_dir)
    if not db_path.exists():
        raise SnapshotError(f"no database at {db_path}")
    recorded = _load_state(db_path, out_dir)
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=60.0,
                           isolation_level=None)
    try:
        # One read transaction, every table from the same moment, held only for the
        # fingerprints and the rows of the files that changed: the CSV is built and written
        # after it is released, so a writer waits as little as possible.
        conn.execute("BEGIN")
        if not _table_info(conn, "marks") or not conn.execute("SELECT 1 FROM marks LIMIT 1").fetchone():
            raise SnapshotError("no marks on file: nothing to export (press Pull Bloomberg now first)")
        rows: Dict[str, int] = {}
        ddl: Dict[str, str] = {}
        plan: List[_File] = []
        info = _table_info(conn, INSTRUMENTS)
        cols, key = _columns(info)
        where = "WHERE instrument_id IN (SELECT DISTINCT instrument_id FROM marks)"
        fp_row = conn.execute(f"SELECT {_aggregates(conn, INSTRUMENTS, info)} FROM {_q(INSTRUMENTS)} {where}").fetchone()
        plan.append(_plan(conn, recorded, out_dir, f"{INSTRUMENTS}.csv", INSTRUMENTS, cols, key, fp_row, where))
        rows[INSTRUMENTS] = plan[-1].count
        for table in MARKET_TABLES:
            info = _table_info(conn, table)
            if not info:
                continue
            if table == MARKS:
                months, first, last = _marks_plan(conn, recorded, out_dir, info)
                plan += months
                rows[table] = sum(f.count for f in months)
            else:
                cols, key = _columns(info)
                fp_row = conn.execute(f"SELECT {_aggregates(conn, table, info)} FROM {_q(table)}").fetchone()
                plan.append(_plan(conn, recorded, out_dir, f"{table}.csv", table, cols, key, fp_row))
                rows[table] = plan[-1].count
            ddl[table] = _ddl(conn, table)
    finally:
        conn.close()
    out_dir.mkdir(parents=True, exist_ok=True)
    written, files = _write_files(out_dir, plan, recorded)
    _save_state(db_path, out_dir, files)
    last_pull, status_changed = _copy_pull_status(db_path, out_dir)
    if status_changed:
        written.append(PULL_STATUS)
    # The same market data as the last export keeps that export's manifest, time included,
    # so exporting twice commits nothing the second time.
    previous = read_manifest(out_dir)
    if not written and previous and previous.get("rows") == rows and previous.get("ddl") == ddl:
        return previous, written
    manifest = {
        "exported_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "marks_from": first, "marks_through": last, "rows": rows, "ddl": ddl, "last_pull": last_pull,
    }
    (out_dir / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest, written + [MANIFEST]


def read_manifest(in_dir: Union[str, Path] = SNAPSHOT_DIR) -> Optional[dict]:
    path = Path(in_dir) / MANIFEST
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _typed(text: str, decl: str):
    """A CSV cell back to what SQLite held. Converted here, not left to SQLite's column
    affinity, so a REAL reads back to the identical float. A cell that does not parse stays
    the text it was: a stored value that is not a number is a data error on the Bloomberg
    PC, and it must show as one here too, not vanish."""
    decl = (decl or "").upper()
    try:
        if "INT" in decl:
            return int(text)
        if "REAL" in decl or "FLOA" in decl or "DOUB" in decl:
            value = float(text)
            return value if value == value else text
    except ValueError:
        pass
    return text


def _read_rows(path: Path, info: List[tuple]) -> tuple:
    """(columns, rows) of `path` limited to the columns this database's table has too, so a
    snapshot from a database with an extra column still loads."""
    decl = {c[1]: c[2] for c in info}
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        keep = [(i, name) for i, name in enumerate(header) if name in decl]
        rows = [tuple(_typed(r[i], decl[name]) for i, name in keep) for r in reader if r]
    return [name for _, name in keep], rows


def _insert(conn: sqlite3.Connection, table: str, cols: List[str], rows: List[tuple], verb: str) -> int:
    before = conn.total_changes
    conn.executemany(
        f"{verb} INTO {_q(table)} ({', '.join(_q(c) for c in cols)}) VALUES ({', '.join('?' * len(cols))})",
        rows)
    return conn.total_changes - before


def _marks_files(in_dir: Path) -> List[Path]:
    """The snapshot's marks files: marks/<YYYY-MM>.csv in name order (2026-09-30), else an
    older snapshot's single marks.csv; [] when there is neither."""
    months = sorted((in_dir / MARKS_DIR).glob("*.csv"))
    if months:
        return months
    legacy = in_dir / LEGACY_MARKS
    return [legacy] if legacy.exists() else []


def import_snapshot(db_path: Union[str, Path], in_dir: Union[str, Path] = SNAPSHOT_DIR,
                    as_of: Optional[str] = None) -> dict:
    """Load the snapshot in `in_dir` into `db_path` (created with the schema if absent), in
    one transaction, then freeze the settled trades as of `as_of` (today by default).
    Returns {'manifest', 'rows': {table: loaded}, 'dropped': {table: rows removed first},
    'instruments_added', 'skipped_marks', 'contract_dates', 'ledger'}; 'contract_dates' is
    `apply_contract_dates`' own result ({'checked', 'updated', 'missing_dates'}), run after
    the load and before the freeze so the ledger sees Bloomberg's expiries (None if that
    module cannot be imported). Reads marks by month (marks/*.csv) or an older snapshot's
    single marks.csv. Raises SnapshotError, touching nothing, when `in_dir` holds neither."""
    from data.ingest.schema import connect

    in_dir = Path(in_dir)
    marks_files = _marks_files(in_dir)
    if not marks_files:
        raise SnapshotError(f"no snapshot in {in_dir} (git pull first; it is written on the "
                            f"Bloomberg PC by marks-export)")
    out = {"manifest": read_manifest(in_dir), "rows": {}, "dropped": {}, "instruments_added": 0,
           "skipped_marks": 0, "contract_dates": None, "ledger": None}
    conn = connect(Path(db_path))
    try:
        with conn:
            inst_path = in_dir / f"{INSTRUMENTS}.csv"
            if inst_path.exists():
                cols, rows = _read_rows(inst_path, _table_info(conn, INSTRUMENTS))
                out["instruments_added"] = _insert(conn, INSTRUMENTS, cols, rows, "INSERT OR IGNORE")
            known = {r[0] for r in conn.execute("SELECT instrument_id FROM instruments")}
            ddl = (out["manifest"] or {}).get("ddl") or {}
            for table in MARKET_TABLES:
                paths = marks_files if table == MARKS else [p for p in [in_dir / f"{table}.csv"] if p.exists()]
                if paths and not _table_info(conn, table) and table == CONTRACT_STATIC:
                    _ensure_contract_static(conn)
                if paths and not _table_info(conn, table) and ddl.get(table):
                    conn.execute(ddl[table])  # a table this database never created: the source's own DDL
                info = _table_info(conn, table)
                if not paths or not info:
                    continue
                out["dropped"][table] = conn.execute(
                    f"DELETE FROM {_q(table)} WHERE source <> ?", (KEPT_SOURCE,)).rowcount
                loaded = 0
                for path in paths:  # each file with its own header: a month kept from an older export may differ
                    cols, rows = _read_rows(path, info)
                    if table == MARKS:
                        at = cols.index("instrument_id")
                        n = len(rows)
                        rows = [r for r in rows if r[at] in known]
                        out["skipped_marks"] += n - len(rows)
                    _insert(conn, table, cols, rows, "INSERT OR REPLACE")
                    loaded += len(rows)
                out["rows"][table] = loaded
        if as_of is None:
            # The book date (New York, rolled at 17:00 New York), never the PC's local date: a
            # PC in Asia is a day ahead until early afternoon, and this freeze must be as of
            # the same day every screen and every pull is on.
            from data.bloomberg.live import book_today
            as_of = book_today().isoformat()
        # Before the freeze, not after: a commodity future booked here at the estimated
        # expiry would otherwise be frozen (or left open) against the wrong date, and its
        # imported FUTURE_PX rows, keyed at Bloomberg's date, would not meet its leg.
        out["contract_dates"] = _apply_contract_dates(conn)
        out["ledger"] = _realise(conn, as_of)
    finally:
        conn.close()
    return out


def _git(repo_root: Path, *args: str, timeout: int = 60) -> tuple:
    r = subprocess.run(["git", *args], cwd=str(repo_root), capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def commit_snapshot(repo_root: Union[str, Path], manifest: dict, push: bool = True,
                    rel: str = SNAPSHOT_REL) -> dict:
    """Commit the snapshot folder alone (`git add -- <rel>`, `git commit -- <rel>`: nothing
    else staged or edited on that PC goes with it) and, with `push`, push it. Returns
    {'committed': bool, 'pushed': bool, 'message': one line for the log}. Never raises: a
    missing git, a folder that is not a clone, a failed commit or push are all reported in
    `message`. An identical snapshot (the export left every file as it was) commits nothing."""
    repo_root = Path(repo_root)
    if not (repo_root / ".git").exists():
        return {"committed": False, "pushed": False, "message": "not a git clone: snapshot written, nothing committed"}
    try:
        _git(repo_root, "add", "--", rel)
        unchanged, _, _ = _git(repo_root, "diff", "--cached", "--quiet", "--", rel)
        if unchanged == 0:
            committed, note = False, "the snapshot already committed is identical: nothing to commit"
        else:
            code, _, err = _git(repo_root, "commit", "-m", f"Bloomberg marks snapshot {manifest.get('exported_at', '')}: "
                                f"marks through {manifest.get('marks_through', '')}", "--", rel)
            if code != 0:
                return {"committed": False, "pushed": False,
                        "message": f"git commit failed: {(err.splitlines() or ['no detail'])[-1]}"}
            committed, note = True, "committed"
        if not push:
            return {"committed": committed, "pushed": False, "message": note}
        code, _, err = _git(repo_root, "push", "origin", "HEAD", timeout=120)
        if code != 0:
            return {"committed": committed, "pushed": False,
                    "message": f"{note}; git push failed: {(err.splitlines() or ['no detail'])[-1]} (run  git push  yourself)"}
        return {"committed": committed, "pushed": True, "message": f"{note}; pushed"}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"committed": False, "pushed": False,
                "message": f"git unavailable ({exc.__class__.__name__}): snapshot written, commit it yourself"}


def save_after_pull(db_path: Union[str, Path], repo_root: Union[str, Path] = REPO_ROOT,
                    out_dir: Optional[Union[str, Path]] = None, commit: bool = False, push: bool = False,
                    log=print) -> dict:
    """The saving every Bloomberg pull ends with (module docstring): the export, and only
    with `commit` a commit of the folder (and with `push` a push) -- the pull itself never
    commits (user, 2026-09-22: "I will trigger the commit and push myself"). Returns
    {'exported': bool, 'committed', 'pushed', 'message'}; logs one line; never raises.
    `RISK_SNAPSHOT=0` in the environment switches it off (a developer's clone)."""
    if os.environ.get("RISK_SNAPSHOT", "1") == "0":
        return {"exported": False, "committed": False, "pushed": False, "message": "marks snapshot off (RISK_SNAPSHOT=0)"}
    repo_root = Path(repo_root)
    out_dir = Path(out_dir) if out_dir is not None else repo_root / SNAPSHOT_REL
    try:
        manifest, written = _export(db_path, out_dir)
    except SnapshotError as exc:
        out = {"exported": False, "committed": False, "pushed": False, "message": f"marks snapshot: {exc}"}
        log(out["message"])
        return out
    except Exception as exc:  # noqa: BLE001 -- the pull must not fail over its saving step
        out = {"exported": False, "committed": False, "pushed": False,
               "message": f"marks snapshot: export failed ({type(exc).__name__}: {exc})"}
        log(out["message"])
        return out
    marks = f"marks snapshot: {manifest['rows'].get('marks', 0)} marks through {manifest['marks_through']}"
    head = f"{marks} written to {SNAPSHOT_REL}/" if written else f"{marks}, {SNAPSHOT_REL}/ already up to date"
    if not commit:
        out = {"exported": True, "committed": False, "pushed": False,
               "message": head + (" (commit and push it yourself)" if written else "")}
        log(out["message"])
        return out
    result = commit_snapshot(repo_root, manifest, push=push, rel=str(out_dir.relative_to(repo_root)).replace(os.sep, "/")
                             if out_dir.is_relative_to(repo_root) else SNAPSHOT_REL)
    out = {"exported": True, **result, "message": f"{head}; {result['message']}"}
    log(out["message"])
    return out


def _ensure_contract_static(conn: sqlite3.Connection) -> None:
    """contract-master's own DDL for contract_static; the snapshot's copy only if that
    package cannot be imported here."""
    try:
        from data.contracts import ensure_static_table
    except ImportError:
        return
    ensure_static_table(conn)


def _apply_contract_dates(conn: sqlite3.Connection) -> Optional[dict]:
    """Bloomberg's stored contract dates onto this PC's commodity futures (instrument expiry,
    NOTIONAL legs, FUTURE_PX mark keys), as the pull does on the Bloomberg PC. Imported at
    call time and guarded like `_realise`. Returns its result unchanged."""
    try:
        from data.ingest.contract_dates import apply_contract_dates
    except ImportError:
        return None
    return apply_contract_dates(conn)


def _realise(conn: sqlite3.Connection, as_of: str) -> Optional[dict]:
    """The backfill's closing step (`backfill._realise_after_backfill`: the ledger's plain
    `realise_settled`), which no pull runs on a PC without Bloomberg. Guarded like the
    backfill's own import: this package must not depend on engine.pnl being importable.
    Returns `live.ledger_block` (2026-09-22): realised, unrealisable, repaired, refrozen,
    kept, refrozen_count and refrozen_summary passed through as the ledger gave them."""
    try:
        from engine.pnl.ledger import realise_settled
    except ImportError:
        return None
    from data.bloomberg.live import ledger_block
    return ledger_block(realise_settled(conn, as_of), as_of)
