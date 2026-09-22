"""The nm-dashboard market history, read for the Risk tab's metrics and for nothing else.

The nm-dashboard (the user's other project, its `fx_alpha` package) keeps a Bloomberg
cache of daily closes as parquet files, one column per series on a `DatetimeIndex`
named `date`. `load_history` finds that folder and loads the three files the risk
metrics use:

  * `bbg_raw_fx_marks.parquet` (`History.spot`): USD per ONE unit of the series
    (AUD 0.71, CHF 1.22, JPY 0.0064, BRL 0.19), `SPX` the S&P 500 level and `XAU` USD
    per ounce; for the NDF currencies (KRW, IDR, INR, TWD, BRL) the column is the 1M NDF
    outright, not spot (it equals 1 / `<CCY>_NDF1M` of the dashboard's NDF file), which
    is also what the Ladder prices those currencies at.
  * `bbg_raw_fx_yields.parquet` (`History.yields`): short rates in percent per series,
    `USD` included, `XAU` pinned to 0 and `SPX` equal to `USD`; the carry term of the
    daily P&L (`engine.risk.metrics`). Optional: without it the metrics run on the
    spot move alone and say so.
  * `bbg_raw_rates.parquet` (`History.swap_rates`): par swap rates in percent,
    columns `<CCY>_SWAP<tenor>` (`USD_SWAP10Y` = 4.589). Optional: without it the
    rates rows have no history and say so.

Where the folder is: the environment variable `RISK_HISTORY_DIR` when set (that path
and no other), else the freshest of the `DEFAULT_DIRS` that exist, siblings of this
repository (`../nm-dashboard/fx_alpha/bbg_data`, `../nm-dashboard/bbg_data`, which is
where the dashboard's own loader reads, and `../bbg_data/bbg_data`, the data repo's
copy): the one whose spot file has the latest last date (its index alone is read), a
tie keeping that order, so a stale copy never wins by position (user decision
2026-09-22). `History.note` says which copies were seen and how far each runs. The
files are the ones the dashboard pulled on the Bloomberg PC; nothing here asks
Bloomberg for anything (hard rule 8).

This history is a RISK input only. Nothing read here is ever written to `marks`, used
as a mark, or used for P&L or delta (hard rule 2): the positions the metrics apply it
to come from `engine.ladder.positions.book_positions`, the app's own official marks.
`load_history` never opens the database.

A missing folder or spot file, or a file that will not read, gives `available = False`
and a plain-language `reason` naming the paths tried; never an exception to the caller.
The result is cached per (folder, file mtimes), like `ui/tabs/blotter_pricing.py`'s
database-mtime cache, so the tab's re-renders do not re-read the parquet files until
the dashboard pulls again.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import pandas as pd

ENV_VAR = "RISK_HISTORY_DIR"
_REPO = Path(__file__).resolve().parents[2]
DEFAULT_DIRS: Tuple[Path, ...] = (
    _REPO.parent / "nm-dashboard" / "fx_alpha" / "bbg_data",
    _REPO.parent / "nm-dashboard" / "bbg_data",
    _REPO.parent / "bbg_data" / "bbg_data",
)
SPOT_FILE = "bbg_raw_fx_marks.parquet"
YIELDS_FILE = "bbg_raw_fx_yields.parquet"
RATES_FILE = "bbg_raw_rates.parquet"
FILES: Dict[str, str] = {"spot": SPOT_FILE, "yields": YIELDS_FILE, "swap_rates": RATES_FILE}


@dataclass
class History:
    """The loaded history. `files` has one entry per file of `FILES`: {file, path, loaded,
    rows, columns, first_date, last_date, reason}. `last_date` is the spot file's last
    date (ISO). `status()` is the JSON-friendly summary `book_risk` publishes."""
    available: bool
    path: str
    reason: str = ""
    spot: pd.DataFrame = field(default_factory=pd.DataFrame)
    yields: pd.DataFrame = field(default_factory=pd.DataFrame)
    swap_rates: pd.DataFrame = field(default_factory=pd.DataFrame)
    last_date: Optional[str] = None
    files: Dict[str, dict] = field(default_factory=dict)
    note: str = ""                                   # which copies were seen, and how far each runs
    candidates: List[dict] = field(default_factory=list)   # [{path, exists, last_date}], in DEFAULT_DIRS order

    def status(self) -> dict:
        return {"available": self.available, "path": self.path, "last_date": self.last_date,
                "reason": self.reason, "note": self.note, "candidates": [dict(c) for c in self.candidates],
                "files": {k: dict(v) for k, v in self.files.items()}}


_LAST_DATE_MEMO: Dict[tuple, Optional[str]] = {}


def spot_last_date(folder: Path) -> Optional[str]:
    """The last date (ISO) of the folder's spot file, from its index alone; None when there
    is no readable spot file. Memoised by the file's mtime."""
    path = folder / SPOT_FILE
    try:
        key = (str(path), os.path.getmtime(path))
    except OSError:
        return None
    if key in _LAST_DATE_MEMO:
        return _LAST_DATE_MEMO[key]
    last: Optional[str] = None
    try:
        import pyarrow.parquet as pq
        index = pd.to_datetime(pq.read_table(path, columns=["date"]).column("date").to_pandas())
    except Exception:  # noqa: BLE001 -- an index stored under another name, or a file that will not read
        try:
            index = pd.to_datetime(pd.read_parquet(path).index)
        except Exception:  # noqa: BLE001
            index = pd.DatetimeIndex([])
    if len(index):
        last = pd.Timestamp(index.max()).strftime("%Y-%m-%d")
    if len(_LAST_DATE_MEMO) > 16:
        _LAST_DATE_MEMO.clear()
    _LAST_DATE_MEMO[key] = last
    return last


def candidates() -> List[dict]:
    """Every folder considered, in order: [{path, exists, last_date}]. With `RISK_HISTORY_DIR`
    set it is that path alone."""
    env = os.environ.get(ENV_VAR, "").strip()
    dirs = (Path(env).expanduser(),) if env else DEFAULT_DIRS
    out = []
    for p in dirs:
        exists = p.is_dir()
        out.append({"path": str(p), "exists": exists, "last_date": spot_last_date(p) if exists else None})
    return out


def history_dir() -> Tuple[Optional[Path], List[dict], str]:
    """(the folder to read, or None; the candidates, in order; a note saying which copies
    were seen and how far each runs). `RISK_HISTORY_DIR` when set is the only candidate;
    otherwise the existing candidate whose spot file runs latest wins, a tie keeping the
    order, and one with no readable spot file only when no other exists."""
    seen = candidates()
    existing = [c for c in seen if c["exists"]]
    if not existing:
        return None, seen, ""
    dated = [c for c in existing if c["last_date"]]
    chosen = max(dated, key=lambda c: c["last_date"]) if dated else existing[0]    # max keeps the first of a tie
    if os.environ.get(ENV_VAR, "").strip():
        note = f"{ENV_VAR} = {chosen['path']}" + (f" (to {chosen['last_date']})" if chosen["last_date"] else " (no spot file)")
    else:
        parts = [f"{len(existing)} {'copy' if len(existing) == 1 else 'copies'} found, using {chosen['path']}"
                 + (f" (to {chosen['last_date']})" if chosen["last_date"] else " (no spot file)")]
        for c in existing:
            if c is not chosen:
                parts.append(f"{c['path']} " + (f"ends {c['last_date']}" if c["last_date"] else "has no spot file"))
        note = "; ".join(parts)
    return Path(chosen["path"]), seen, note


def _read_frame(path: Path) -> pd.DataFrame:
    """One parquet file as a float frame on a sorted, de-duplicated DatetimeIndex."""
    df = pd.read_parquet(path)
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df.index.name = "date"
    return df.apply(pd.to_numeric, errors="coerce").astype(float)


def _file_status(path: Path, df: Optional[pd.DataFrame], reason: str) -> dict:
    out = {"file": path.name, "path": str(path), "loaded": df is not None, "rows": 0, "columns": [],
           "first_date": None, "last_date": None, "reason": reason}
    if df is not None:
        out["rows"] = int(len(df))
        out["columns"] = [str(c) for c in df.columns]
        if len(df):
            out["first_date"] = df.index[0].strftime("%Y-%m-%d")
            out["last_date"] = df.index[-1].strftime("%Y-%m-%d")
    return out


def _load(folder: Path) -> History:
    frames: Dict[str, Optional[pd.DataFrame]] = {}
    files: Dict[str, dict] = {}
    for name, filename in FILES.items():
        path = folder / filename
        if not path.is_file():
            frames[name] = None
            files[name] = _file_status(path, None, f"{path} not found")
            continue
        try:
            frames[name] = _read_frame(path)
            files[name] = _file_status(path, frames[name], "")
        except Exception as exc:  # noqa: BLE001 -- a file that will not read is a reason, never a crash
            frames[name] = None
            files[name] = _file_status(path, None, f"{path} could not be read ({type(exc).__name__}: {exc})")
    spot = frames["spot"]
    if spot is None:
        return History(False, str(folder), f"no spot history: {files['spot']['reason']}", files=files)
    if spot.empty:
        return History(False, str(folder), f"spot history {folder / SPOT_FILE} is empty", files=files, spot=spot)
    return History(True, str(folder), "", spot=spot,
                   yields=frames["yields"] if frames["yields"] is not None else pd.DataFrame(),
                   swap_rates=frames["swap_rates"] if frames["swap_rates"] is not None else pd.DataFrame(),
                   last_date=spot.index[-1].strftime("%Y-%m-%d"), files=files)


_CACHE: Dict[tuple, History] = {}
_CACHE_SLOTS = 4


def _cache_key(folder: Path) -> tuple:
    mtimes = []
    for filename in FILES.values():
        p = folder / filename
        try:
            mtimes.append((filename, os.path.getmtime(p)))
        except OSError:
            mtimes.append((filename, None))
    return (str(folder), tuple(mtimes))


def load_history(path: Union[str, Path, None] = None) -> History:
    """The history from `path` (a folder), or from `history_dir()` when None. Cached by
    folder and file mtimes (the note and the candidate list are refreshed on every call,
    they are not part of the key); a folder that does not exist gives `available = False`
    with the paths tried in `reason`."""
    note = ""
    if path is not None:
        folder: Optional[Path] = Path(path).expanduser()
        seen = [{"path": str(folder), "exists": folder.is_dir(), "last_date": None}]
        if not folder.is_dir():
            folder = None
    else:
        folder, seen, note = history_dir()
    if folder is None:
        return History(False, seen[0]["path"] if seen else "",
                       "no market history folder: tried " + ", ".join(c["path"] for c in seen), candidates=seen)
    key = _cache_key(folder)
    hit = _CACHE.get(key)
    if hit is None:
        hit = _load(folder)
        if len(_CACHE) >= _CACHE_SLOTS:
            _CACHE.clear()
        _CACHE[key] = hit
    hit.note = note
    hit.candidates = seen
    return hit
