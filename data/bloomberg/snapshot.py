"""Market-data snapshot: carry the marks on file from the Bloomberg PC to a PC with no
Terminal, through git.

The database (`data/raw/risk.db`) is never committed, so a PC without Bloomberg has no
marks and every USD figure is blank. `export_snapshot` writes the market-data tables as
plain CSV under `data/bbg_snapshot/` (tracked by git, one file per table, rows in
primary-key order so a re-export diffs small); `import_snapshot` reads them back on the
other PC. Every "Pull Bloomberg now" ends with `save_after_pull` (user, 2026-09-22: "I
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
table a pull writes (MARKET_TABLES: the OIS curves and their quotes, the fixings, the FX
and rates vol quotes, the dividend yields), the `instruments` rows those marks hang off,
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
pull ever runs here to do it.
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
import subprocess
from datetime import date, datetime, timezone
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
MARKET_TABLES = ("marks", "curves", "curve_quotes", "index_fixings",
                 "vol_quotes", "rate_vol_quotes", "equity_dividend_yields")
KEPT_SOURCE = "MANUAL"
PULL_STATUS = "pull_status.json"


class SnapshotError(Exception):
    """Nothing to export, or no snapshot to import: said in plain words by the launcher."""


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_info(conn: sqlite3.Connection, table: str) -> List[tuple]:
    """PRAGMA table_info rows: (cid, name, type, notnull, dflt_value, pk); [] if absent."""
    return conn.execute(f"PRAGMA table_info({_q(table)})").fetchall()


def _write_table(conn: sqlite3.Connection, table: str, path: Path, where: str = "") -> tuple:
    """(rows written, whether the file's bytes changed)."""
    info = _table_info(conn, table)
    cols = [c[1] for c in info]
    key = [c[1] for c in sorted((c for c in info if c[5]), key=lambda c: c[5])] or cols
    sql = (f"SELECT {', '.join(_q(c) for c in cols)} FROM {_q(table)} {where} "
           f"ORDER BY {', '.join(_q(c) for c in key)}")
    tmp = path.with_suffix(path.suffix + ".tmp")
    n = 0
    # lineterminator '\n' on every OS: the same book must give the same bytes from Windows
    # and the Mac, or each export would rewrite every line. csv writes a float with repr(),
    # which reads back to the identical float.
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(cols)
        for row in conn.execute(sql):
            w.writerow(row)
            n += 1
    changed = not path.exists() or path.read_bytes() != tmp.read_bytes()
    os.replace(tmp, path)
    return n, changed


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
    db_path, out_dir = Path(db_path), Path(out_dir)
    if not db_path.exists():
        raise SnapshotError(f"no database at {db_path}")
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=60.0,
                           isolation_level=None)
    try:
        conn.execute("BEGIN")  # one read transaction: every table from the same moment
        if not _table_info(conn, "marks") or not conn.execute("SELECT 1 FROM marks LIMIT 1").fetchone():
            raise SnapshotError("no marks on file: nothing to export (press Pull Bloomberg now first)")
        out_dir.mkdir(parents=True, exist_ok=True)
        rows: Dict[str, int] = {}
        ddl: Dict[str, str] = {}
        rows[INSTRUMENTS], changed = _write_table(
            conn, INSTRUMENTS, out_dir / f"{INSTRUMENTS}.csv",
            "WHERE instrument_id IN (SELECT DISTINCT instrument_id FROM marks)")
        for table in MARKET_TABLES:
            if _table_info(conn, table):
                rows[table], table_changed = _write_table(conn, table, out_dir / f"{table}.csv")
                changed = changed or table_changed
                ddl[table] = _ddl(conn, table)
        first, last = conn.execute("SELECT MIN(as_of_date), MAX(as_of_date) FROM marks").fetchone()
    finally:
        conn.close()
    last_pull, status_changed = _copy_pull_status(db_path, out_dir)
    changed = changed or status_changed
    # The same market data as the last export keeps that export's manifest, time included,
    # so exporting twice commits nothing the second time.
    previous = read_manifest(out_dir)
    if not changed and previous and previous.get("rows") == rows and previous.get("ddl") == ddl:
        return previous
    manifest = {
        "exported_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "marks_from": first, "marks_through": last, "rows": rows, "ddl": ddl, "last_pull": last_pull,
    }
    (out_dir / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


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


def import_snapshot(db_path: Union[str, Path], in_dir: Union[str, Path] = SNAPSHOT_DIR,
                    as_of: Optional[str] = None) -> dict:
    """Load the snapshot in `in_dir` into `db_path` (created with the schema if absent), in
    one transaction, then freeze the settled trades as of `as_of` (today by default).
    Returns {'manifest', 'rows': {table: loaded}, 'dropped': {table: rows removed first},
    'instruments_added', 'skipped_marks', 'ledger'}. Raises SnapshotError, touching
    nothing, when `in_dir` holds no marks.csv."""
    from data.ingest.schema import connect

    in_dir = Path(in_dir)
    if not (in_dir / "marks.csv").exists():
        raise SnapshotError(f"no snapshot in {in_dir} (git pull first; it is written on the "
                            f"Bloomberg PC by marks-export)")
    out = {"manifest": read_manifest(in_dir), "rows": {}, "dropped": {}, "instruments_added": 0,
           "skipped_marks": 0, "ledger": None}
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
                path = in_dir / f"{table}.csv"
                if path.exists() and not _table_info(conn, table) and ddl.get(table):
                    conn.execute(ddl[table])  # a table this database never created: the source's own DDL
                info = _table_info(conn, table)
                if not path.exists() or not info:
                    continue
                cols, rows = _read_rows(path, info)
                if table == "marks":
                    at = cols.index("instrument_id")
                    n = len(rows)
                    rows = [r for r in rows if r[at] in known]
                    out["skipped_marks"] = n - len(rows)
                out["dropped"][table] = conn.execute(
                    f"DELETE FROM {_q(table)} WHERE source <> ?", (KEPT_SOURCE,)).rowcount
                _insert(conn, table, cols, rows, "INSERT OR REPLACE")
                out["rows"][table] = len(rows)
        out["ledger"] = _realise(conn, as_of or date.today().isoformat())
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
        manifest = export_snapshot(db_path, out_dir)
    except SnapshotError as exc:
        out = {"exported": False, "committed": False, "pushed": False, "message": f"marks snapshot: {exc}"}
        log(out["message"])
        return out
    except Exception as exc:  # noqa: BLE001 -- the pull must not fail over its saving step
        out = {"exported": False, "committed": False, "pushed": False,
               "message": f"marks snapshot: export failed ({type(exc).__name__}: {exc})"}
        log(out["message"])
        return out
    head = f"marks snapshot: {manifest['rows'].get('marks', 0)} marks through {manifest['marks_through']} written to {SNAPSHOT_REL}/"
    if not commit:
        out = {"exported": True, "committed": False, "pushed": False, "message": head + " (commit and push it yourself)"}
        log(out["message"])
        return out
    result = commit_snapshot(repo_root, manifest, push=push, rel=str(out_dir.relative_to(repo_root)).replace(os.sep, "/")
                             if out_dir.is_relative_to(repo_root) else SNAPSHOT_REL)
    out = {"exported": True, **result, "message": f"{head}; {result['message']}"}
    log(out["message"])
    return out


def _realise(conn: sqlite3.Connection, as_of: str) -> Optional[dict]:
    """The backfill's closing step (`backfill._freeze_ndfs_at_present_spot`), which no pull
    runs on a PC without Bloomberg. Guarded like the backfill's own import: this package
    must not depend on engine.pnl being importable."""
    try:
        from engine.pnl.ledger import realise_settled
    except ImportError:
        return None
    return realise_settled(conn, as_of, ndf_present_spot=True)
