"""The commodity settlement history, read from the research app's database for the Risk
tab's metrics and for nothing else.

The research app next door (`../Commodity Dashboard`, its `rvapp/db/schema.sql`) keeps
Bloomberg's daily settlement of every contract month it follows:

  * `instrument` (`instrument_id` 'NYMEX:CL', the same 'EXCHANGE:CODE' id as our
    `config/contracts.csv` `root_id`; `price_scale`, `currency`, `bbg_root`);
  * `contract` (`contract_id` 'CLZ26 Comdty', `instrument_id`, `year`, `month`,
    `last_trade_date`);
  * `price_daily` (`contract_id`, `date`, `settle` RAW: Bloomberg's quoted price;
    `settle x price_scale` is the price in the instrument's `quote_unit`), rows only for
    days the contract traded, and only while it was among the root's nearest
    `calendar_depth` contracts;
  * `fx_daily` (`pair` 'USDCNH', `date`, `rate` = units of the second currency per one
    of the first).

What is served (all on `CommodityHistory`, from `load_commodity_history`):

  * `settle_series(contract_id)`: the contract's settlements in its QUOTE UNITS
    (raw x price_scale).
  * `constant_maturity_series(root_id, months_ahead)`: on each date the settlement of
    the `months_ahead`-th listed contract (1 = the front), listed meaning the root's
    contracts whose last trade date is after that date, in last-trade order: the
    series rolls ON a contract's last trade date (that day already shows the next
    one). `constant_maturity_changes` is its day-on-day change, always taken on one
    contract (the one ranked on the later day), so a roll never shows as a price jump.
  * `fx_series(pair)` as stored, and `usd_per_unit(currency)`.
  * `daily_pnl_series_for_position(...)`: the USD P&L a held position would have made
    each day: the contract's own settlement changes while it has history, and before
    its first settlement the constant-maturity changes at the rank the contract holds
    on the as-of date (its months to expiry on the listed strip). Each change is in
    RAW quoted price x our `multiplier` x lots, because our multiplier is the
    quote-currency amount per 1.0 of Bloomberg's quoted price (`data/contracts/
    universe.py`), which is the research app's raw settle: a change in quote units
    (raw x price_scale) times our multiplier would be price_scale times too small
    (NYMEX:RB, CBOT:ZC and the other 0.01 roots). A non-USD contract converts at that
    day's USD per quote unit from the research app's own pair (CNY through USDCNH by
    default, the research app's own rule; USDCNY only when asked), the last rate on or
    before the day within `FX_TOLERANCE_DAYS`. Returns are summed P&L, never re-marked.
  * `contract_liquidity(contract_ids, as_of, window=20)` (also a module function): each
    contract's latest open interest and its average daily volume over the last `window`
    days, in the research contract's lots, for a liquidity check of position size; the
    caveats (Chinese counting, LME, the research depth) are on the module function.
  * `window_move(root_id, months_to_expiry, start, end)` (also a module function on the
    default history): the fractional settlement change of the one contract that was
    `months_to_expiry` months out on the `start` close, held to the `end` close with no
    roll (a contract expiring inside the window keeps its own last settle); the
    historical replays of the commodity stress use it.

Contract ids: our canonical id and the research app's share the form ('CLZ26 Comdty'),
but not always the Bloomberg root: where the research app still has its placeholder
root ('ZZWR') and `config/contracts.csv` has the Phase 2 guess ('WR'), the ids differ.
A contract id not found as it is, is matched on (root, contract year, contract month),
the month code and the two-digit year read from the id itself.

This history is a RISK input only. Nothing read here is ever written to `marks`, used as
a mark, or used for P&L or delta (hard rule 2), and nothing here asks Bloomberg for
anything (hard rule 8). The database is opened read-only (`mode=ro`), one short
connection per read, never held: the research app runs it in WAL mode and keeps
writing.

Where the database is: the environment variable `COMMODITY_HISTORY_DB` when set (that
path and no other), else `DEFAULT_PATH`, `../Commodity Dashboard/var/rv.sqlite` next to
this repository. With none found, or a file that is not the research app's database,
`available` is False with a plain-language `reason` naming the paths tried, and every
series is empty with that reason in `series.attrs["reason"]`: never an exception to the
caller. Every series carries `attrs["reason"]` ('' when it has data).

  * `research_curve(root_id, as_of, db_path=None)` (module function): the research app's
    settlement of every contract of the root on its latest date on or before `as_of`, in
    quote units, one row per contract with its month as 'YYYY-MM'. Context for the Curve
    and Data tabs beside our own official marks, labelled 'research': never a mark.
  * `research_source(db_path=None)` (module function; also `status()`'s `source_kind` /
    `source_note` and every curve and liquidity record's): whether the rows are Bloomberg's
    ('real'), generated by the research app's mock provider ('mock') or 'unknown', from the
    research app's `job` table (2026-09-29: the dev PC's copy is mock, and its figures were once
    read as real). Anything drawn from 'mock' history is for layout only.
  * `research_price_check(conn, as_of)` (module function): each root of our open futures, our
    latest official FUTURE_PX (else the average fill) against the research app's latest settle,
    each LME metal's cash price against its front monthly (20 %), and with China exposure the
    research USDCNH / USDCNY against our spot (2 %), flagged with the factor.

The result is cached per (path, the database's and its WAL file's mtime and size): in
WAL mode the main file's mtime does not move on a write until a checkpoint, so the WAL
file is part of the key. Inside that cached object, and so per database identity, are
kept: the settlements read per root (one query per root, or one query for many roots through
`prefetch_roots`), the constant-maturity frame per (root, months ahead)
and each position's P&L per argument set (2026-09-25: the Risk tab and the header's VaR
chip rebuilt them on every call, about 11 s of a 14 s `book_risk`). A changed database
is a new object, so nothing kept outlives the data it was built from.
"""
from __future__ import annotations

import bisect
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Mapping
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

from data.contracts.tickers import month_from_code, padded_root, parse_bbg_ticker, parse_option_ticker
from data.contracts.universe import load_roots

__all__ = ["CHECK_KINDS", "CommodityHistory", "ContractMap", "DEFAULT_FX_PAIR", "DEFAULT_PATH", "ENV_VAR", "FX_CHECK_THRESHOLD",
           "LABEL", "PRICE_CHECK_THRESHOLD", "SOURCE_KINDS", "candidates", "latest_settles", "warm", "contract_liquidity", "load_commodity_history", "research_curve",
           "research_price_check", "research_source", "window_move"]

LABEL = "research"

ENV_VAR = "COMMODITY_HISTORY_DB"
_REPO = Path(__file__).resolve().parents[2]
DEFAULT_PATH = _REPO.parent / "Commodity Dashboard" / "var" / "rv.sqlite"
TABLES = ("instrument", "contract", "price_daily", "fx_daily")
CONNECT_TIMEOUT_SECONDS = 5.0
FX_TOLERANCE_DAYS = 7
# The liquidity check (`contract_liquidity`): days in the volume average, and how old the
# latest open interest may be before the note says so.
LIQUIDITY_WINDOW = 20
LIQUIDITY_STALE_DAYS = 7
CHINA_EXCHANGES = ("SHFE", "DCE", "ZCE", "INE", "GFEX")
_CHINA_NOTE = ("{exchange}: open interest and volume as the research app stores them from Bloomberg, no factor "
               "applied; the Chinese exchanges counted open interest double-sided until January 2020 and "
               "single-sided since (the research app's note), and whether Bloomberg's figures for this contract "
               "are single- or double-sided is not verified on a terminal")
_LME_ID_RE = re.compile(r"^(?P<root>LME:[A-Z0-9]+)\s+(?P<prompt>\d{4}-\d{2}-\d{2})$")


def _our_root_of(contract_id: str) -> Optional[str]:
    """Our root id for a canonical contract id, by its Bloomberg root in `config/contracts.csv`;
    None when no root or more than one has it (the id is then matched as it is)."""
    parts = parse_bbg_ticker(contract_id)
    if parts is None:
        return None
    try:
        roots = load_roots()
    except Exception:  # noqa: BLE001 -- no universe: the id is matched as it is
        return None
    hits = [r.root_id for r in roots.values()
            if r.bbg_root.strip().upper() == parts[0] and r.bbg_yellow_key.upper() == parts[3].upper()]
    return hits[0] if len(hits) == 1 else None


def _our_lot(root_id: Optional[str]) -> Optional[Tuple[float, str]]:
    """(contract_size, size_unit) of our root, or None."""
    if not root_id:
        return None
    try:
        r = load_roots().get(_norm(root_id).replace(" ", ""))
    except Exception:  # noqa: BLE001 -- no universe: no lot comparison
        return None
    return (float(r.contract_size), r.size_unit) if r is not None else None
# The pair each currency converts through when the caller names none (the research
# app's fx_daily rule: "convert CNY prices with USDCNH unless a spread names USDCNY").
DEFAULT_FX_PAIR = {"CNY": "USDCNH", "CNH": "USDCNH"}


class ContractMap(Mapping):
    """The date -> research contract map a constant-maturity series carries in `attrs['contracts']`,
    read-only. pandas deep-copies a Series' attrs on every operation (a copy, an arithmetic step,
    a slice), and a plain dict of a few thousand dates made that about 1.9 s of a profiled
    `book_risk` (2026-09-29). This map is immutable, so a deep copy returns it as it is, and the
    dict is built only when first read. It reads like the dict it replaces: `m[date]`,
    `m.get(date)`, `len`, iteration, `items()`, `dict(m)`, equality with a dict."""
    __slots__ = ("_source", "_dict")

    def __init__(self, source: pd.Series):
        self._source = source         # date -> contract id; never modified here
        self._dict = None

    def _built(self) -> dict:
        if self._dict is None:
            self._dict = self._source.to_dict()
        return self._dict

    def __getitem__(self, key):
        return self._built()[key]

    def __iter__(self):
        return iter(self._built())

    def __len__(self) -> int:
        return len(self._source)

    def __contains__(self, key) -> bool:
        return key in self._built()

    def __eq__(self, other) -> bool:
        if other is self:
            return True
        return Mapping.__eq__(self, other)

    __hash__ = None

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self

    def __reduce__(self):
        return (_contract_map_from_dict, (self._built(),))

    def __repr__(self) -> str:
        return f"ContractMap({len(self)} dates)"


def _contract_map_from_dict(d: dict) -> "ContractMap":
    return ContractMap(pd.Series(d, dtype=object))


def _empty(reason: str, name: Optional[str] = None) -> pd.Series:
    s = pd.Series([], index=pd.DatetimeIndex([], name="date"), dtype=float, name=name)
    s.attrs["reason"] = reason
    return s


def _copy(s: pd.Series) -> pd.Series:
    """A kept series handed out with its own attrs (a caller may set attrs on what it gets).
    The values are a shallow copy: pandas 3's copy-on-write copies them the moment either side
    is written, so a caller that modifies what it gets never reaches the kept series, and one
    that only reads (every caller today) pays for no copy. The kept series is never modified
    here, so two threads may hand out the same one at once."""
    out = s.copy(deep=False)
    out.attrs = {k: (dict(v) if isinstance(v, dict) else v) for k, v in s.attrs.items()}
    return out


def _connect(path: Path) -> sqlite3.Connection:
    """A read-only connection (`mode=ro`): any write through it raises. Close it at once."""
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=CONNECT_TIMEOUT_SECONDS)


def _query(path: Path, sql: str, params: tuple = ()) -> pd.DataFrame:
    conn = _connect(path)
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


def _norm(text: str) -> str:
    return " ".join(str(text or "").split()).upper()


# ------------------------------------------------------------------ provenance
# Who wrote the research rows (2026-09-29, after the dev PC's figures were read as real). The
# research app records every run in its `job` table with the data provider it used
# ('mock' | 'bloomberg' | ''); its rows carry no provider of their own, so the rows are as real
# as the data jobs (kinds pull and backfill, any status but queued: a failed or cancelled run
# may have written part of its rows) that fed them.
SOURCE_KINDS = ("real", "mock", "unknown")
REAL_PROVIDERS = ("bloomberg",)
MOCK_PROVIDERS = ("mock",)
DATA_JOB_KINDS = ("pull", "backfill")
MOCK_CONSEQUENCE = "risk figures are for layout only"


def _unknown_source(sentence: str) -> dict:
    return {"source_kind": "unknown", "source_note": sentence, "source_providers": [], "source_jobs": []}


def _job_text(jobs: List[dict], shown: int = 3) -> str:
    """'job 1 pull 2026-09-28' / 'jobs 1 pull 2026-09-28, 4 backfill 2026-09-29, +2 more'."""
    parts = [f"{j['job_id']} {j['kind']} {(j['finished_at'] or j['created_at'] or '')[:10]}".strip()
             for j in jobs[-shown:]]
    more = f", +{len(jobs) - shown} more" if len(jobs) > shown else ""
    return ("job " if len(jobs) == 1 else "jobs ") + ", ".join(parts) + more


def _read_source(conn: sqlite3.Connection, path) -> dict:
    """`research_source` from an open read-only connection. Never raises."""
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "job" not in names:
            return _unknown_source(f"research history's provider is unknown: {path} has no job table, so "
                                   "whether its prices are Bloomberg's or generated cannot be told")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(job)")}
        if "provider" not in cols:
            return _unknown_source(f"research history's provider is unknown: {path}'s job table has no provider "
                                   "column")
        rows = conn.execute(
            "SELECT job_id, kind, status, provider, created_at, finished_at FROM job WHERE kind IN ("
            + ",".join("?" * len(DATA_JOB_KINDS)) + ") AND status <> 'queued' ORDER BY job_id",
            DATA_JOB_KINDS).fetchall()
    except Exception as exc:  # noqa: BLE001 -- an unreadable job table is a reason, never a crash
        return _unknown_source(f"research history's provider is unknown: {path}'s job table could not be read "
                               f"({type(exc).__name__}: {exc})")
    jobs = [{"job_id": r[0], "kind": r[1], "status": r[2], "provider": str(r[3] or "").strip().lower(),
             "created_at": r[4], "finished_at": r[5]} for r in rows]
    providers = sorted({j["provider"] for j in jobs})
    out = {"source_kind": "unknown", "source_note": "", "source_providers": providers, "source_jobs": jobs}
    if not jobs:
        out["source_note"] = (f"research history's provider is unknown: {path} records no data job (pull or "
                              "backfill), so whether its prices are Bloomberg's or generated cannot be told")
        return out
    mock = [j for j in jobs if j["provider"] in MOCK_PROVIDERS]
    other = [j for j in jobs if j["provider"] not in MOCK_PROVIDERS + REAL_PROVIDERS]
    named = ", ".join(p or "(blank)" for p in providers)
    if mock:
        partly = "" if len(mock) == len(jobs) else "partly "
        out["source_kind"] = "mock"
        out["source_note"] = (f"research history is {partly}mock data (provider {named}, {_job_text(mock)}): "
                              f"{MOCK_CONSEQUENCE}")
    elif other:
        out["source_note"] = (f"research history's provider is unknown (provider {named}, {_job_text(other)}): "
                              "not known to be Bloomberg's prices")
    else:
        out["source_kind"] = "real"
        out["source_note"] = f"research history from Bloomberg (provider {named}, {_job_text(jobs)})"
    return out


_SOURCE_CACHE: Dict[tuple, dict] = {}


def research_source(db_path: Union[str, Path, None] = None) -> dict:
    """Who wrote the research database's prices, read from its `job` table (read-only, cached per
    database identity like the history). Never raises.

    Returns {source_kind, source_note, source_providers, source_jobs, path}:
      source_kind       'real' when every data job (pull / backfill, any status but queued) used a
                        real provider ('bloomberg'); 'mock' when any used 'mock' (its rows may
                        survive a later Bloomberg pull); 'unknown' with no database, no job table,
                        no data job, or a provider that is neither;
      source_note       one plain sentence, e.g. "research history is mock data (provider mock,
                        job 1 pull 2026-09-28): risk figures are for layout only";
      source_providers  the providers of those jobs, sorted; source_jobs: [{job_id, kind, status,
                        provider, created_at, finished_at}] in job order;
      path              the database read ('' when none)."""
    if db_path is not None:
        seen = [Path(db_path).expanduser()]
    else:
        seen = [Path(c["path"]) for c in candidates()]
    found = [p for p in seen if p.is_file()]
    if not found:
        out = _unknown_source("research history's provider is unknown: no research database (tried "
                              + ", ".join(str(p) for p in seen) + ")")
        out["path"] = ""
        return out
    db = found[0]
    key = _cache_key(db)
    hit = _SOURCE_CACHE.get(key)
    if hit is None:
        try:
            conn = _connect(db)
            try:
                hit = _read_source(conn, db)
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 -- a file that will not open is a reason, never a crash
            hit = _unknown_source(f"research history's provider is unknown: {db} could not be read "
                                  f"({type(exc).__name__}: {exc})")
        if len(_SOURCE_CACHE) >= _CACHE_SLOTS:
            _SOURCE_CACHE.clear()
        _SOURCE_CACHE[key] = hit
    out = dict(hit, source_providers=list(hit["source_providers"]),
               source_jobs=[dict(j) for j in hit["source_jobs"]])
    out["path"] = str(db)
    return out


@dataclass
class CommodityHistory:
    """The research app's history. `status()` is the JSON-friendly summary for the tab."""
    available: bool
    path: str
    reason: str = ""
    last_date: Optional[str] = None
    first_date: Optional[str] = None
    instruments: pd.DataFrame = field(default_factory=pd.DataFrame)   # index instrument_id
    contracts: pd.DataFrame = field(default_factory=pd.DataFrame)     # index contract_id
    fx: pd.DataFrame = field(default_factory=pd.DataFrame)            # date x pair, rate as stored
    note: str = ""
    candidates: List[dict] = field(default_factory=list)              # [{path, exists}]
    source: dict = field(default_factory=lambda: _unknown_source(""))  # research_source(): who wrote the rows
    _roots: Dict[str, pd.DataFrame] = field(default_factory=dict, repr=False)
    _lookup: Dict[str, dict] = field(default_factory=dict, repr=False)
    _cm: Dict[tuple, pd.DataFrame] = field(default_factory=dict, repr=False)      # (root, months_ahead) -> frame
    _pnl: Dict[tuple, pd.Series] = field(default_factory=dict, repr=False)        # position P&L per argument set
    # Serialises the per-root reads, so a request arriving while `warm()` reads the same roots in
    # the background waits for that read instead of doing it twice (2026-09-29).
    _read_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    # ----------------------------------------------------------------- summary
    def status(self) -> dict:
        return {"available": self.available, "path": self.path, "reason": self.reason,
                "first_date": self.first_date, "last_date": self.last_date, "note": self.note,
                "candidates": [dict(c) for c in self.candidates],
                "roots": int(len(self.instruments)), "contracts": int(len(self.contracts)),
                "fx_pairs": [str(c) for c in self.fx.columns],
                "source_kind": self.source["source_kind"], "source_note": self.source["source_note"],
                "source_providers": list(self.source["source_providers"]),
                "source_jobs": [dict(j) for j in self.source["source_jobs"]]}

    @property
    def source_kind(self) -> str:
        """'real' | 'mock' | 'unknown': see `research_source`."""
        return self.source["source_kind"]

    @property
    def source_note(self) -> str:
        """The plain sentence naming who wrote the research rows: see `research_source`."""
        return self.source["source_note"]

    # ------------------------------------------------------------------ lookups
    def _root_row(self, root_id: str) -> Tuple[Optional[pd.Series], str]:
        if not self.available:
            return None, self.reason
        key = _norm(root_id).replace(" ", "")
        if key not in self.instruments.index:
            return None, f"root {root_id} is not in the research database ({self.path})"
        return self.instruments.loc[key], ""

    def resolve_contract(self, contract_id: str, root_id: Optional[str] = None) -> Tuple[Optional[str], str]:
        """(the research app's contract id, '') or (None, reason). Tried as it is first, then
        on (root, year, month) from the id's month code and two-digit year; `root_id` names
        the root when the id's Bloomberg root differs from the research app's."""
        if not self.available:
            return None, self.reason
        if "ids" not in self._lookup:
            self._lookup["ids"] = {_norm(c): c for c in self.contracts.index}
        found = self._lookup["ids"].get(_norm(contract_id))
        if found is not None:
            if root_id and self.contracts.at[found, "instrument_id"] != _norm(root_id).replace(" ", ""):
                return None, (f"contract {contract_id} belongs to {self.contracts.at[found, 'instrument_id']} "
                              f"in the research database, not {root_id}")
            return found, ""
        parts = parse_bbg_ticker(contract_id)
        if parts is None or not root_id:
            return None, f"contract {contract_id} is not in the research database ({self.path})"
        _, code, digits, _ = parts
        month = month_from_code(code)
        year = int(digits) + 2000 if len(digits) == 2 else self._one_digit_year(int(digits))
        root = _norm(root_id).replace(" ", "")
        hit = self._by_month().get((root, year, month))
        if hit is None:
            return None, f"contract {contract_id} ({root_id} {code}{year}) is not in the research database ({self.path})"
        return hit, ""

    def _by_month(self) -> Dict[tuple, str]:
        """(instrument_id, year, month) -> the first contract id with them in `contracts` order,
        built once per object: the same pick as filtering the frame and taking its first row (a
        float year or month equals and hashes as the int asked for; a missing one matches
        nothing)."""
        table = self._lookup.get("by_month")
        if table is None:
            table = {}
            c = self.contracts
            for cid, inst, year, month in zip(c.index, c["instrument_id"], c["year"], c["month"]):
                table.setdefault((inst, year, month), cid)
            self._lookup["by_month"] = table
        return table

    def _one_digit_year(self, digit: int) -> int:
        ref = int((self.last_date or "2026")[:4])
        year = (ref // 10) * 10 + digit
        return year + 10 if year < ref - 1 else year

    # ------------------------------------------------------------------ prices
    @staticmethod
    def _wide(df: pd.DataFrame) -> pd.DataFrame:
        """date x contract_id of RAW settles from long rows (contract_id, date, settle) with
        `date` already a datetime. A plain pivot: (contract_id, date) is `price_daily`'s primary
        key, so no two rows ever share a cell."""
        if df.empty:
            wide = pd.DataFrame(index=pd.DatetimeIndex([], name="date"))
        else:
            wide = df.pivot(index="date", columns="contract_id", values="settle").sort_index()
            wide.index.name = "date"
        return wide.astype(float)

    def prefetch_roots(self, root_ids) -> None:
        """Read the settlements of several roots in ONE query (our root ids or the research
        app's, e.g. the roots of every position the Risk tab is about to ask for) into the same
        per-root store `_root_prices` fills one root at a time, so a later call per root is
        served from memory. Roots already read or not in the research database are left alone.
        A read that fails is not kept and raises nothing: each root then reads on its own and
        reports its reason there."""
        if not self.available:
            return
        with self._read_lock:
            self._prefetch(root_ids)

    def _prefetch(self, root_ids) -> None:
        wanted = sorted({_norm(r).replace(" ", "") for r in root_ids} - set(self._roots))
        wanted = [r for r in wanted if r in self.instruments.index]
        if not wanted:
            return
        try:
            df = _query(Path(self.path),
                        "SELECT c.instrument_id, p.contract_id, p.date, p.settle FROM price_daily p "
                        "JOIN contract c ON c.contract_id = p.contract_id WHERE c.instrument_id IN ("
                        + ",".join("?" * len(wanted)) + ")", tuple(wanted))
        except Exception:  # noqa: BLE001 -- the per-root read will give the reason
            return
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
        parts = {root: part.drop(columns="instrument_id") for root, part in df.groupby("instrument_id", sort=False)}
        for root in wanted:
            self._roots[root] = self._wide(parts.get(root, df.iloc[0:0]))

    def _root_prices(self, root_id: str) -> pd.DataFrame:
        """date x contract_id of RAW settles for one root (research ids), memoised. A read that
        fails gives an empty frame with `attrs['reason']`, not memoised."""
        hit = self._roots.get(root_id)
        if hit is not None:
            return hit
        with self._read_lock:
            return self._read_root(root_id)

    def _read_root(self, root_id: str) -> pd.DataFrame:
        if root_id not in self._roots:
            try:
                df = _query(Path(self.path),
                            "SELECT p.contract_id, p.date, p.settle FROM price_daily p "
                            "JOIN contract c ON c.contract_id = p.contract_id WHERE c.instrument_id = ?",
                            (root_id,))
            except Exception as exc:  # noqa: BLE001 -- a failed read is a reason, never a crash
                wide = pd.DataFrame(index=pd.DatetimeIndex([], name="date"))
                wide.attrs["reason"] = f"{self.path} could not be read ({type(exc).__name__}: {exc})"
                return wide
            if not df.empty:
                df["date"] = pd.to_datetime(df["date"])
            self._roots[root_id] = self._wide(df)
        return self._roots[root_id]

    def _raw_settles(self, research_id: str) -> pd.Series:
        root = self.contracts.at[research_id, "instrument_id"]
        wide = self._root_prices(root)
        if research_id not in wide.columns:
            return _empty(wide.attrs.get("reason")
                          or f"contract {research_id} has no settlements in the research database ({self.path})")
        return wide[research_id].dropna()

    def settle_series(self, contract_id: str, root_id: Optional[str] = None) -> pd.Series:
        """The contract's daily settlements in its quote units (raw x the instrument's
        price_scale), on the dates it traded. Empty with `attrs['reason']` when there is none."""
        research_id, why = self.resolve_contract(contract_id, root_id)
        if research_id is None:
            return _empty(why, contract_id)
        scale = float(self.instruments.at[self.contracts.at[research_id, "instrument_id"], "price_scale"])
        raw = self._raw_settles(research_id)
        if raw.empty:
            return _empty(raw.attrs.get("reason") or f"contract {contract_id} has no settlements", contract_id)
        out = (raw * scale).rename(contract_id)
        out.attrs.update(reason="", research_contract_id=research_id, price_scale=scale)
        return out

    # ------------------------------------------------------- constant maturity
    def _strip(self, root: str) -> Tuple[List[str], List[str]]:
        """The root's contracts in last-trade order and their last trade dates (ISO), kept per
        root on this object. Callers never modify the lists returned."""
        strips = self._lookup.setdefault("strips", {})
        if root not in strips:
            c = self.contracts[self.contracts["instrument_id"] == root]
            c = c.sort_values(["last_trade_date", "year", "month"])
            strips[root] = (list(c.index), list(c["last_trade_date"]))
        return strips[root]

    def _ranked(self, root: str, months_ahead: int, dates: pd.DatetimeIndex) -> List[Optional[str]]:
        """For each date, the `months_ahead`-th contract (1 = front) whose last trade date is
        after the date; None beyond the strip."""
        ids, ltds = self._strip(root)
        out: List[Optional[str]] = []
        for d in dates.strftime("%Y-%m-%d"):
            i = bisect.bisect_right(ltds, d) + months_ahead - 1
            out.append(ids[i] if i < len(ids) else None)
        return out

    def _cm_frame(self, root_id: str, months_ahead: int, start=None) -> Tuple[Optional[pd.DataFrame], str]:
        """The constant-maturity frame (date x contract_id, raw, raw_change, settle, change),
        built once per (root, months_ahead) and kept on this object, which is itself cached per
        database identity (`load_commodity_history`), so a changed database builds it afresh.
        Callers never modify the frame returned."""
        row, why = self._root_row(root_id)
        if row is None:
            return None, why
        if int(months_ahead) < 1:
            return None, f"months_ahead must be 1 or more (1 = the front contract), not {months_ahead}"
        root = row.name
        key = (root, int(months_ahead))
        frame = self._cm.get(key)
        if frame is None:
            wide = self._root_prices(root)
            if wide.empty:
                return None, wide.attrs.get("reason") or f"root {root_id} has no settlements in the research database ({self.path})"
            frame = self._build_cm_frame(root, int(months_ahead), wide, float(row["price_scale"]))
            self._cm[key] = frame
        if start is not None:
            frame = frame[frame.index >= pd.Timestamp(start)]
        return frame, ""

    def _build_cm_frame(self, root: str, months_ahead: int, wide: pd.DataFrame, scale: float) -> pd.DataFrame:
        """On each date, the ranked contract's raw settle and its day-on-day change (both on that
        contract), NaN where the contract is beyond the strip or has no settlement. Positional
        numpy picks, the same values the per-date lookups gave."""
        diffs = wide.diff()                       # day-on-day on the root's own trading days
        ranked = self._ranked(root, months_ahead, wide.index)
        pos = wide.columns.get_indexer(pd.Index(ranked, dtype=object))
        ok = pos >= 0
        rows = np.arange(len(wide))
        level = np.full(len(wide), np.nan)
        change = np.full(len(wide), np.nan)
        level[ok] = wide.to_numpy(dtype=float)[rows[ok], pos[ok]]
        change[ok] = diffs.to_numpy(dtype=float)[rows[ok], pos[ok]]
        frame = pd.DataFrame({"contract_id": ranked, "raw": level, "raw_change": change}, index=wide.index)
        frame["settle"] = frame["raw"] * scale
        frame["change"] = frame["raw_change"] * scale
        return frame

    def constant_maturity_series(self, root_id: str, months_ahead: int, start=None) -> pd.Series:
        """The settlement (quote units) of the `months_ahead`-th listed contract on each date,
        rolled on the contract's last trade date. `attrs['contracts']` is the contract used
        per date (a read-only `ContractMap`, cheap for pandas to carry). Level only: its day-on-day difference jumps at a roll; use
        `constant_maturity_changes` for returns."""
        frame, why = self._cm_frame(root_id, months_ahead, start)
        name = f"{root_id} #{months_ahead}"
        if frame is None:
            return _empty(why, name)
        frame = frame.dropna(subset=["settle"])
        if frame.empty:
            return _empty(f"root {root_id} has no settlement for contract #{months_ahead} in the research database", name)
        out = frame["settle"].rename(name)
        out.attrs.update(reason="", contracts=ContractMap(frame["contract_id"]))
        return out

    def constant_maturity_changes(self, root_id: str, months_ahead: int, start=None, raw: bool = False) -> pd.Series:
        """Day-on-day settlement change of the constant-maturity contract, both days on the
        contract ranked on the later day, so a roll never shows as a jump. Quote units, or
        Bloomberg's raw quoted price with `raw=True`. A day whose contract did not trade on
        the root's previous trading day is left out."""
        frame, why = self._cm_frame(root_id, months_ahead, start)
        name = f"{root_id} #{months_ahead}"
        if frame is None:
            return _empty(why, name)
        col = "raw_change" if raw else "change"
        frame = frame.dropna(subset=[col])
        if frame.empty:
            return _empty(f"root {root_id} has no day-on-day change for contract #{months_ahead}", name)
        out = frame[col].rename(name)
        out.attrs.update(reason="", contracts=ContractMap(frame["contract_id"]))
        return out

    def months_ahead_of(self, contract_id: str, root_id: str, as_of=None) -> Tuple[Optional[int], str]:
        """The contract's rank on the listed strip on `as_of` (default: the database's last
        date): 1 = the front. A contract already expired on that date counts as the front."""
        research_id, why = self.resolve_contract(contract_id, root_id)
        if research_id is None:
            return None, why
        return self._rank_of(research_id, as_of), ""

    def _rank_of(self, research_id: str, as_of=None) -> int:
        """`months_ahead_of` for a contract already resolved to its research id."""
        root = self.contracts.at[research_id, "instrument_id"]
        ids, ltds = self._strip(root)
        ref = pd.Timestamp(as_of or self.last_date).strftime("%Y-%m-%d")
        first_listed = bisect.bisect_right(ltds, ref)
        pos = ids.index(research_id)
        return max(1, pos - first_listed + 1)

    # ------------------------------------------------------------ window moves
    def window_move_detail(self, root_id: str, months_to_expiry: float, start, end) -> dict:
        """The fractional settlement change of ONE contract from the `start` close to the
        `end` close: {move, reason, contract_id, start_date, end_date, start_settle,
        end_settle} (settles raw). The start close is the root's last trading day on or
        before `start`; the contract is the one listed then (last trade date after it) whose
        last trade date is nearest `months_to_expiry` months away (30.4375 days a month, the
        nearer one on a tie), and it must have settled on the start close. No roll: the end
        close is that contract's last settlement on or before `end`, so a contract that
        expires inside the window keeps its own last settle. `move` is None with a reason
        when there is no history, or the start settle is not above zero. Kept on this object
        per argument set (each call gets its own copy); a result whose read failed is not kept."""
        try:
            key = ("window", str(root_id), float(months_to_expiry), str(start), str(end))
        except (TypeError, ValueError):
            key = None
        memo = self._lookup.setdefault("windows", {})
        if key is not None and key in memo:
            return dict(memo[key])
        out = self._window_move_detail(root_id, months_to_expiry, start, end)
        if key is not None and "could not be read" not in out["reason"]:
            memo[key] = dict(out)
        return out

    def _window_move_detail(self, root_id: str, months_to_expiry: float, start, end) -> dict:
        out = {"move": None, "reason": "", "contract_id": None, "start_date": None, "end_date": None,
               "start_settle": None, "end_settle": None}
        row, why = self._root_row(root_id)
        if row is None:
            out["reason"] = why
            return out
        try:
            t0, t1 = pd.Timestamp(start), pd.Timestamp(end)
        except (TypeError, ValueError) as exc:
            out["reason"] = f"window {start} to {end} is not a pair of dates ({exc})"
            return out
        if t1 <= t0:
            out["reason"] = f"window {start} to {end}: the end is not after the start"
            return out
        root = row.name
        wide = self._root_prices(root)
        if wide.empty:
            out["reason"] = wide.attrs.get("reason") or f"root {root_id} has no settlements in the research database ({self.path})"
            return out
        before = wide.index[wide.index <= t0]
        if before.empty:
            out["reason"] = (f"root {root_id} has no settlement on or before {t0:%Y-%m-%d} in the research database "
                             f"(it starts {wide.index[0]:%Y-%m-%d})")
            return out
        d0 = before[-1]
        ids, ltds = self._strip(root)
        first = bisect.bisect_right(ltds, d0.strftime("%Y-%m-%d"))
        listed = list(zip(ids[first:], ltds[first:]))
        if not listed:
            out["reason"] = f"root {root_id} has no contract listed on {d0:%Y-%m-%d} in the research database"
            return out
        target = float(months_to_expiry)
        cid, _ = min(listed, key=lambda c: (abs((pd.Timestamp(c[1]) - d0).days / 30.4375 - target), c[1]))
        out["contract_id"] = cid
        own = wide[cid].dropna() if cid in wide.columns else pd.Series(dtype=float)
        if d0 not in own.index:
            out["reason"] = (f"contract {cid} ({months_to_expiry:g} months out on {d0:%Y-%m-%d}) has no settlement "
                             f"on that day in the research database")
            return out
        after = own[(own.index > d0) & (own.index <= t1)]
        if after.empty:
            out["reason"] = f"contract {cid} has no settlement after {d0:%Y-%m-%d} up to {t1:%Y-%m-%d} in the research database"
            return out
        s0, s1 = float(own.loc[d0]), float(after.iloc[-1])
        out.update(start_date=d0.strftime("%Y-%m-%d"), end_date=after.index[-1].strftime("%Y-%m-%d"),
                   start_settle=s0, end_settle=s1)
        if s0 <= 0:
            out["reason"] = f"contract {cid} settled at {s0:g} on {d0:%Y-%m-%d}: no fractional move from a price not above zero"
            return out
        out["move"] = s1 / s0 - 1.0
        return out

    def window_move(self, root_id: str, months_to_expiry: float, start, end) -> Tuple[Optional[float], str]:
        """(fractional settlement change of one contract over the window, '') or (None, reason);
        see `window_move_detail`."""
        d = self.window_move_detail(root_id, months_to_expiry, start, end)
        return d["move"], d["reason"]

    # -------------------------------------------------------------------- curve
    def research_curve(self, root_id: str, as_of) -> dict:
        """The root's futures curve in the research app on its latest settlement date on or
        before `as_of` (None = the database's last date). See the module function
        `research_curve` for the shape. Never raises."""
        out = {"root_id": root_id, "research_root": None, "available": bool(self.available), "label": LABEL,
               "path": self.path, "candidates": [dict(c) for c in self.candidates], "as_of": None, "date": None,
               "stale_days": None, "unit": "", "currency": "", "price_scale": None, "reason": "", "note": "",
               "source": "", "source_kind": self.source_kind, "source_note": self.source_note, "rows": []}
        if not self.available:
            out["reason"] = self.reason
            return out
        if as_of is None:
            as_of = self.last_date
        try:
            ts = pd.Timestamp(as_of)
        except (TypeError, ValueError) as exc:
            out["reason"] = f"as-of {as_of!r} is not a date ({exc})"
            return out
        if pd.isna(ts):
            out["reason"] = f"as-of {as_of!r} is not a date"
            return out
        ts = ts.normalize()
        out["as_of"] = ts.strftime("%Y-%m-%d")
        row, why = self._root_row(root_id)
        if row is None:
            out["reason"] = why
            return out
        root = row.name
        unit = row.get("quote_unit")
        out.update(research_root=root, unit=str(unit) if isinstance(unit, str) else "",
                   currency=str(row["currency"]), price_scale=float(row["price_scale"]))
        wide = self._root_prices(root)
        if wide.attrs.get("reason"):
            out["reason"] = wide.attrs["reason"]
            return out
        if wide.empty:
            out["reason"] = f"root {root_id} has no settlements in the research database ({self.path})"
            return out
        before = wide.index[wide.index <= ts]
        if before.empty:
            out["reason"] = (f"root {root_id} has no settlement on or before {out['as_of']} in the research database "
                             f"(it starts {wide.index[0]:%Y-%m-%d})")
            return out
        day = before[-1]
        settles = wide.loc[day].dropna()
        scale = float(row["price_scale"])
        info = self.contracts.loc[[c for c in settles.index if c in self.contracts.index]]
        rows = []
        for cid, raw in settles.items():
            if cid not in info.index:
                continue
            year, month = int(info.at[cid, "year"]), int(info.at[cid, "month"])
            ltd = info.at[cid, "last_trade_date"]
            rows.append({"contract_id": str(cid), "month": f"{year:04d}-{month:02d}", "year": year,
                         "month_no": month, "expiry": str(ltd) if isinstance(ltd, str) and ltd else None,
                         "settle": float(raw) * scale, "raw_settle": float(raw)})
        rows.sort(key=lambda r: (r["month"], r["contract_id"]))
        out["date"] = day.strftime("%Y-%m-%d")
        out["stale_days"] = int((ts - day).days)
        out["rows"] = rows
        mine = self.contracts[self.contracts["instrument_id"] == root]
        listed = int((mine["last_trade_date"].astype(str) >= out["date"]).sum())
        notes = []
        if listed > len(rows):
            depth = row.get("calendar_depth")
            kept = f", the research app keeps the nearest {int(depth)}" if pd.notna(depth) else ""
            notes.append(f"{listed - len(rows)} of the {listed} contracts listed on {out['date']} have no settlement "
                         f"that day{kept}")
        if out["stale_days"]:
            notes.append(f"settlements of {out['date']}, {out['stale_days']} day(s) before {out['as_of']}")
        out["note"] = "; ".join(notes)
        out["source"] = (f"research app database {self.path}, {root} settlements of {out['date']} "
                         f"(latest on or before {out['as_of']}), in {out['unit'] or 'quote units'}")
        return out

    # ---------------------------------------------------------------------- fx
    def fx_series(self, pair: str) -> pd.Series:
        """The pair's daily rate as the research app stores it (second currency per one of the first)."""
        if not self.available:
            return _empty(self.reason, pair)
        key = _norm(pair).replace(" ", "")
        if key not in self.fx.columns:
            return _empty(f"FX pair {pair} is not in the research database ({self.path}); it has "
                          + (", ".join(self.fx.columns) or "none"), pair)
        out = self.fx[key].dropna().rename(key)
        out.attrs["reason"] = ""
        return out

    def usd_per_unit(self, currency: str, pair: Optional[str] = None) -> pd.Series:
        """USD per one unit of `currency`, daily, from `pair` or the default pair: USDCNH for
        CNY (and CNH), else USD<ccy> inverted, else <ccy>USD as stored. `attrs['pair']` names it."""
        ccy = _norm(currency)
        if ccy == "USD":
            s = _empty("USD needs no conversion (1 USD per USD)", "USD")
            s.attrs["pair"] = ""
            return s
        if pair is None:
            pair = DEFAULT_FX_PAIR.get(ccy)
            if pair is None:
                pair = f"USD{ccy}" if f"USD{ccy}" in self.fx.columns or f"{ccy}USD" not in self.fx.columns else f"{ccy}USD"
        pair = _norm(pair).replace(" ", "")
        rate = self.fx_series(pair)
        if rate.empty:
            rate.attrs["pair"] = pair
            return rate
        if pair.startswith("USD"):
            out = (1.0 / rate).rename(ccy)
        elif pair.endswith("USD"):
            out = rate.rename(ccy)
        else:
            return _empty(f"FX pair {pair} has no USD side", ccy)
        out.attrs.update(reason="", pair=pair)
        return out

    # ---------------------------------------------------------------- position
    def daily_pnl_series_for_position(self, root_id: str, contract_id: str, lots: float, multiplier: float,
                                      currency: str, fx: Union[str, pd.Series, None] = None,
                                      as_of=None, start=None) -> pd.Series:
        """USD P&L per day of `lots` contracts held (sign = direction), the book held constant:
        raw settle change x `multiplier` (ours: quote currency per 1.0 of Bloomberg's quoted
        price) x lots x USD per quote unit that day. Own history where the contract has
        settlements; on and before its first settlement the constant-maturity change at the
        rank the contract holds on `as_of` (default: the database's last date). `fx`: None =
        the default pair for `currency`, a pair name, or a Series of USD per quote unit.
        attrs: reason, research_contract_id, months_ahead, own_from, fallback_days, fx_pair,
        fx_missing_days.

        Kept on this object per argument set (a given `fx` Series is never kept), so a second
        call with the same arguments on the same database is served from memory; each call
        gets its own copy. A result whose read failed is not kept."""
        key = None
        if not isinstance(fx, pd.Series):
            try:
                key = (str(root_id), str(contract_id), float(lots), float(multiplier), str(currency),
                       None if fx is None else str(fx), None if as_of is None else str(as_of),
                       None if start is None else str(start))
            except (TypeError, ValueError):
                key = None                   # not a number: computed as it stands, never kept
        if key is not None:
            hit = self._pnl.get(key)
            if hit is not None:
                return _copy(hit)
        out = self._position_pnl(root_id, contract_id, lots, multiplier, currency, fx, as_of, start)
        if key is not None and "could not be read" not in str(out.attrs.get("reason", "")):
            self._pnl[key] = out
            return _copy(out)
        return out

    def _position_pnl(self, root_id: str, contract_id: str, lots: float, multiplier: float,
                      currency: str, fx, as_of, start) -> pd.Series:
        name = contract_id
        row, why = self._root_row(root_id)
        if row is None:
            return _empty(why, name)
        if _norm(currency) != _norm(row["currency"]):
            return _empty(f"{root_id} is quoted in {row['currency']} in the research database, not {currency}", name)
        research_id, why = self.resolve_contract(contract_id, root_id)
        if research_id is None:
            return _empty(why, name)
        n = self._rank_of(research_id, as_of)     # = months_ahead_of, without resolving twice
        own = self._raw_settles(research_id)
        own_change = own.diff().iloc[1:]
        # The constant-maturity raw changes straight off the kept frame: the values of
        # `constant_maturity_changes(root, n, raw=True)` without its `contracts` attr, a
        # date -> contract dict that pandas deep-copied on every later operation.
        frame, _ = self._cm_frame(row.name, n)
        cm = frame["raw_change"].dropna() if frame is not None else None
        if cm is None or cm.empty:
            cm = _empty("")
        if not own.empty:
            cm = cm[cm.index <= own.index[0]]
        change = pd.concat([cm, own_change]).sort_index()
        change = change[~change.index.duplicated(keep="last")]
        if start is not None:
            change = change[change.index >= pd.Timestamp(start)]
        if change.empty:
            depth = row.get("calendar_depth")
            kept = f"; the research app keeps the nearest {int(depth)} contract(s) of {row.name}" if pd.notna(depth) else ""
            return _empty(f"contract {contract_id} has no day-on-day settlement change in the research database "
                          f"({len(own)} settlement(s) of its own, none for contract #{n} of {row.name}{kept})", name)
        local = change * float(multiplier) * float(lots)

        fx_pair, missing = "", 0
        if _norm(currency) != "USD":
            if isinstance(fx, pd.Series):
                conv, fx_pair = fx.dropna().astype(float), "given"
                conv.index = pd.to_datetime(conv.index)
            else:
                conv = self.usd_per_unit(currency, fx)
                fx_pair = conv.attrs.get("pair", "")
                if conv.empty:
                    return _empty(f"no USD conversion for {currency}: {conv.attrs.get('reason', '')}", name)
            conv = conv.sort_index()
            left = pd.DataFrame({"date": local.index, "pnl": local.to_numpy()})
            right = pd.DataFrame({"date": conv.index, "usd": conv.to_numpy()})
            merged = pd.merge_asof(left, right, on="date", direction="backward",
                                   tolerance=pd.Timedelta(days=FX_TOLERANCE_DAYS))
            missing = int(merged["usd"].isna().sum())
            usd = pd.Series((merged["pnl"] * merged["usd"]).to_numpy(), index=local.index)
        else:
            usd = local
        usd = usd.dropna()
        usd.index.name = "date"
        usd = usd.rename(name)
        if usd.empty:
            return _empty(f"contract {contract_id}: no day has both a settlement change and a {fx_pair} rate", name)
        own_from = own.index[0].strftime("%Y-%m-%d") if not own.empty else None
        usd.attrs.update(reason="", research_contract_id=research_id, months_ahead=n, own_from=own_from,
                         fallback_days=int((usd.index <= own.index[0]).sum()) if not own.empty else int(len(usd)),
                         fx_pair=fx_pair, fx_missing_days=missing)
        return usd

    # --------------------------------------------------------------- liquidity
    def _liquidity_target(self, contract_id: str, root_id: Optional[str]) -> Tuple[Optional[str], Optional[str], dict]:
        """(the research contract id, our root id, extras) for one of our contract ids, or
        (None, our root id, extras with 'reason'). Extras: 'notes', 'underlying'."""
        extra: dict = {"notes": [], "underlying": None}
        text = " ".join(str(contract_id or "").split())
        lme = _LME_ID_RE.match(text.upper())
        if lme:
            root = root_id or lme.group("root")
            prompt = lme.group("prompt")
            key = _norm(root).replace(" ", "")
            mine = self.contracts[self.contracts["instrument_id"] == key]
            year, month = int(prompt[:4]), int(prompt[5:7])
            hit = mine[(mine["year"] == year) & (mine["month"] == month)]
            if hit.empty:
                extra["reason"] = (f"LME prompt {prompt}: no {root} contract for {prompt[:7]} in the research "
                                   f"database ({self.path})")
                return None, root, extra
            extra["notes"].append(f"LME: the research app's {hit.index[0]}, the monthly contract of the prompt "
                                  "month; a ticket's own prompt date has no volume or open interest of its own, "
                                  "and whether Bloomberg reports these per monthly prompt is not verified")
            return str(hit.index[0]), root, extra
        opt = parse_option_ticker(text) if parse_bbg_ticker(text) is None else None
        if opt is not None:
            broot, code, digits, _, _, key = opt
            extra["underlying"] = f"{padded_root(broot)}{code}{digits} {key}"
            extra["reason"] = ("an option on a future: the research app keeps open interest and volume for "
                               f"futures only; ask for its underlying {extra['underlying']}")
            return None, root_id or _our_root_of(extra["underlying"]), extra
        root = root_id or _our_root_of(text)
        research_id, why = self.resolve_contract(text, root)
        if research_id is None:
            extra["reason"] = why
            return None, root, extra
        return research_id, root, extra

    def contract_liquidity(self, contract_ids, as_of, window: int = LIQUIDITY_WINDOW) -> Dict[str, dict]:
        """Open interest and average daily volume per contract; see the module function."""
        if isinstance(contract_ids, dict):
            items = [(str(k), (str(v) if v else None)) for k, v in contract_ids.items()]
        else:
            items = [(str(k), None) for k in (contract_ids or [])]
        window = max(1, int(window or LIQUIDITY_WINDOW))
        try:
            day = pd.Timestamp(as_of).normalize()
            as_of_iso = day.strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            day, as_of_iso = None, str(as_of)
        out: Dict[str, dict] = {}
        plan: Dict[str, Tuple[str, dict]] = {}
        for cid, root in items:
            rec = {"contract_id": cid, "root_id": root, "source_contract_id": None, "research_root": None,
                   "open_interest": None, "oi_date": None, "adv": None, "adv_days": 0, "window": window,
                   "volume_last": None, "volume_date": None, "lot_size": None, "lot_unit": "", "exchange": "",
                   "underlying": None, "label": LABEL, "as_of": as_of_iso, "reason": "", "note": "",
                   "source_kind": self.source_kind, "source_note": self.source_note}
            out[cid] = rec
            if not self.available:
                rec["reason"] = self.reason
                continue
            if day is None:
                rec["reason"] = f"as-of {as_of!r} is not a date"
                continue
            research_id, our_root, extra = self._liquidity_target(cid, root)
            rec["root_id"] = our_root
            rec["underlying"] = extra["underlying"]
            if research_id is None:
                rec["reason"] = extra.get("reason") or f"contract {cid} is not in the research database"
                continue
            plan[cid] = (research_id, extra)
        if not plan:
            return out
        wanted = sorted({rid for rid, _ in plan.values()})
        try:
            df = _query(Path(self.path),
                        "SELECT contract_id, date, open_interest, volume FROM price_daily WHERE date <= ? "
                        "AND contract_id IN (" + ",".join("?" * len(wanted)) + ") ORDER BY contract_id, date",
                        (as_of_iso, *wanted))
        except Exception as exc:  # noqa: BLE001 -- a failed read is a reason, never a crash
            for cid in plan:
                out[cid]["reason"] = f"{self.path} could not be read ({type(exc).__name__}: {exc})"
            return out
        groups = {k: g for k, g in df.groupby("contract_id", sort=False)}
        for cid, (rid, extra) in plan.items():
            rec = out[cid]
            notes: List[str] = list(extra["notes"])
            research_root = str(self.contracts.at[rid, "instrument_id"])
            rec.update(source_contract_id=rid, research_root=research_root, exchange=research_root.split(":")[0])
            if research_root in self.instruments.index:
                inst = self.instruments.loc[research_root]
                size = inst.get("contract_size")
                rec["lot_size"] = float(size) if size is not None and pd.notna(size) else None
                rec["lot_unit"] = str(inst.get("size_unit") or "")
                if inst.get("exchange"):
                    rec["exchange"] = str(inst.get("exchange"))
            ours = _our_lot(rec["root_id"])
            if ours and rec["lot_size"] is not None and (abs(ours[0] - rec["lot_size"]) > 1e-9
                                                         or _norm(ours[1]) != _norm(rec["lot_unit"])):
                notes.append(f"a research lot is {rec['lot_size']:g} {rec['lot_unit']}, ours "
                             f"{ours[0]:g} {ours[1]}: the figures are in research lots")
            if rec["exchange"].upper() in CHINA_EXCHANGES:
                notes.append(_CHINA_NOTE.format(exchange=rec["exchange"].upper()))
            g = groups.get(rid)
            if g is None or g.empty:
                rec["reason"] = self._no_liquidity_reason(rid, research_root, as_of_iso)
                rec["note"] = "; ".join(notes)
                continue
            oi = g.dropna(subset=["open_interest"])
            vol = g.dropna(subset=["volume"])
            missing = []
            if oi.empty:
                missing.append("no open interest on file on or before " + as_of_iso)
            else:
                rec["open_interest"] = float(oi["open_interest"].iloc[-1])
                rec["oi_date"] = str(oi["date"].iloc[-1])[:10]
                gap = (day - pd.Timestamp(rec["oi_date"])).days
                if gap > LIQUIDITY_STALE_DAYS:
                    notes.append(f"latest open interest is {gap} days before {as_of_iso}")
            if vol.empty:
                missing.append("no volume on file on or before " + as_of_iso
                               + " (the research app pulls volume only for its most recent history)")
            else:
                last = vol.tail(window)
                rec["adv"] = float(last["volume"].mean())
                rec["adv_days"] = int(len(last))
                rec["volume_last"] = float(vol["volume"].iloc[-1])
                rec["volume_date"] = str(vol["date"].iloc[-1])[:10]
                if rec["adv_days"] < window:
                    notes.append(f"average over {rec['adv_days']} day(s) only, fewer than {window} with volume on file")
            rec["reason"] = "; ".join(missing)
            rec["note"] = "; ".join(notes)
        return out

    def _no_liquidity_reason(self, research_id: str, research_root: str, as_of_iso: str) -> str:
        """Why a resolved contract has no figures on or before `as_of_iso`."""
        ids, ltds = self._strip(research_root)
        depth = None
        if research_root in self.instruments.index:
            d = self.instruments.at[research_root, "calendar_depth"]
            depth = int(d) if d is not None and pd.notna(d) else None
        listed = [c for c, t in zip(ids, ltds) if t > as_of_iso]
        if depth and research_id in listed and listed.index(research_id) + 1 > depth:
            return (f"{research_id} is month {listed.index(research_id) + 1} of the listed strip on {as_of_iso}; "
                    f"the research app keeps figures only for the {depth} nearest {research_root} contracts, "
                    "so a deferred month this far out has none")
        return f"no open interest or volume on file for {research_id} on or before {as_of_iso}"


# ------------------------------------------------------------------------ load
def candidates() -> List[dict]:
    """Every path considered, in order: [{path, exists}]. With `COMMODITY_HISTORY_DB` set it is that path alone."""
    env = os.environ.get(ENV_VAR, "").strip()
    paths = (Path(env).expanduser(),) if env else (DEFAULT_PATH,)
    return [{"path": str(p), "exists": p.is_file()} for p in paths]


def _cache_key(path: Path) -> tuple:
    """(path, the database's (mtime, size), its WAL file's (mtime, size) or None). An empty
    WAL counts as none: a read-only open of a WAL database leaves an empty `-wal` (and a
    `-shm`) beside it, SQLite's own side files, and that must not invalidate the entry."""
    parts = [str(path.resolve())]
    for p in (path, Path(str(path) + "-wal")):
        try:
            st = p.stat()
            parts.append((st.st_mtime_ns, st.st_size) if st.st_size else None)
        except OSError:
            parts.append(None)
    return tuple(parts)


def _load(path: Path) -> CommodityHistory:
    try:
        conn = _connect(path)
        try:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            missing = [t for t in TABLES if t not in names]
            if missing:
                why = f"{path} is not the research app's database (no {', '.join(missing)} table)"
                return CommodityHistory(False, str(path), why,
                                        source=_unknown_source(f"research history's provider is unknown: {why}"))
            inst_cols = {r[1] for r in conn.execute("PRAGMA table_info(instrument)")}
            unit_col = "quote_unit" if "quote_unit" in inst_cols else "'' AS quote_unit"
            extra_cols = ", ".join(c if c in inst_cols else f"NULL AS {c}"
                                   for c in ("contract_size", "size_unit", "exchange"))
            instruments = pd.read_sql_query(
                "SELECT instrument_id, name, sector, currency, price_scale, bbg_root, calendar_depth, "
                f"{unit_col}, {extra_cols} FROM instrument", conn)
            contracts = pd.read_sql_query(
                "SELECT contract_id, instrument_id, year, month, last_trade_date FROM contract", conn)
            fx = pd.read_sql_query("SELECT pair, date, rate FROM fx_daily", conn)
            # Two queries, not one: MIN and MAX together scan the whole date index (0.3 s on the
            # dev copy), each alone is one index seek (2026-09-29, same values).
            first = conn.execute("SELECT MIN(date) FROM price_daily").fetchone()[0]
            last = conn.execute("SELECT MAX(date) FROM price_daily").fetchone()[0]
            source = _read_source(conn, path)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 -- a file that will not read is a reason, never a crash
        why = f"{path} could not be read ({type(exc).__name__}: {exc})"
        return CommodityHistory(False, str(path), why,
                                source=_unknown_source(f"research history's provider is unknown: {why}"))
    instruments = instruments.set_index("instrument_id")
    contracts = contracts.set_index("contract_id")
    if fx.empty:
        fx_wide = pd.DataFrame(index=pd.DatetimeIndex([], name="date"))
    else:
        fx["date"] = pd.to_datetime(fx["date"])
        # A plain pivot: (pair, date) is fx_daily's primary key, so no cell has two rows.
        fx_wide = fx.pivot(index="date", columns="pair", values="rate").sort_index().astype(float)
    if last is None:
        return CommodityHistory(False, str(path), f"{path} holds no settlements (price_daily is empty)",
                                instruments=instruments, contracts=contracts, fx=fx_wide, source=source)
    return CommodityHistory(True, str(path), "", last_date=last, first_date=first, instruments=instruments,
                            contracts=contracts, fx=fx_wide, source=source)


_CACHE: Dict[tuple, CommodityHistory] = {}
_CACHE_SLOTS = 4
_LOAD_LOCK = threading.Lock()   # one load per database identity, even with warm() running


def load_commodity_history(path: Union[str, Path, None] = None) -> CommodityHistory:
    """The research app's history from `path` (the database file), or from `candidates()`
    when None. Cached by (path, database and WAL file mtime and size); a missing file
    gives `available = False` with the paths tried in `reason`."""
    if path is not None:
        seen = [{"path": str(Path(path).expanduser()), "exists": Path(path).expanduser().is_file()}]
    else:
        seen = candidates()
    found = [c for c in seen if c["exists"]]
    if not found:
        tried = ", ".join(c["path"] for c in seen)
        return CommodityHistory(False, seen[0]["path"] if seen else "", "no commodity history database: tried " + tried,
                                candidates=seen, source=_unknown_source(
                                    f"research history's provider is unknown: no research database (tried {tried})"))
    db = Path(found[0]["path"])
    key = _cache_key(db)
    hit = _CACHE.get(key)
    if hit is None:
        with _LOAD_LOCK:
            hit = _CACHE.get(key)
            if hit is None:
                hit = _load(db)
                if len(_CACHE) >= _CACHE_SLOTS:
                    _CACHE.clear()
                _CACHE[key] = hit
    hit.candidates = seen
    hit.note = (f"{ENV_VAR} = {db}" if os.environ.get(ENV_VAR, "").strip() and path is None else f"using {db}") + (
        f" (settlements {hit.first_date} to {hit.last_date})" if hit.last_date else "")
    return hit


def research_curve(root_id: str, as_of, db_path: Union[str, Path, None] = None) -> dict:
    """The research app's futures curve of one root: the settlement of every contract of
    `root_id` (our `config/contracts.csv` root_id, 'NYMEX:CL', the research app's
    instrument_id) on the root's latest settlement date on or before `as_of`. Read-only from
    `price_daily` and `contract` through the cached `load_commodity_history(db_path)`.

    CONTEXT only, labelled 'research': never a mark, never written anywhere, never in P&L or
    delta (hard rule 2), and nothing asks Bloomberg (hard rule 8). Never raises.

    Returns {root_id, research_root, available, label, path, candidates, as_of, date,
    stale_days, unit, currency, price_scale, reason, note, source, source_kind, source_note, rows}
    (source_kind / source_note: `research_source`, whether the rows are Bloomberg's or mock):
      root_id      as given; research_root: the research instrument_id matched (None if none);
      available    True when the research database was found and read;
      label        'research'; path: the database ('' or the first path tried when none);
      candidates   [{path, exists}] tried;
      as_of        ISO; date: the settlement date used (None when no rows);
      stale_days   calendar days from `date` to `as_of` (None when no rows);
      unit         the research quote_unit ('USD/bbl', 'USD/bu', 'CNY/t'); currency;
      price_scale  settle = raw_settle x price_scale;
      reason       '' when there are rows, else why not, in plain words;
      note         '' or what is left out (contracts listed with no settlement that day, a stale date);
      source       one line naming the database, root and date, for a hover;
      rows         sorted by month: {contract_id (the research app's id, e.g. 'CLZ26 Comdty'; a
                   placeholder root reads 'ZZWRF27 Comdty'), month 'YYYY-MM', year, month_no,
                   expiry (the research last_trade_date, ISO; the research app estimates it
                   when Bloomberg has none; None if blank), settle (quote units, float),
                   raw_settle (Bloomberg's quoted price as stored, the unit of our FUTURE_PX
                   marks)}."""
    return load_commodity_history(db_path).research_curve(root_id, as_of)


def window_move(root_id: str, months_to_expiry: float, start, end,
                path: Union[str, Path, None] = None) -> Tuple[Optional[float], str]:
    """`CommodityHistory.window_move` on the history `load_commodity_history(path)` finds:
    (fractional settlement change of the contract `months_to_expiry` months out on `start`,
    held to `end` without a roll, '') or (None, reason)."""
    return load_commodity_history(path).window_move(root_id, months_to_expiry, start, end)


def contract_liquidity(contract_ids, as_of, window: int = LIQUIDITY_WINDOW,
                       db_path: Union[str, Path, None] = None) -> Dict[str, dict]:
    """Each contract's open interest and average daily volume from the research app's
    `price_daily` (Bloomberg's OPEN_INT and PX_VOLUME as it stores them), for a liquidity check
    of position size against the market. Read-only through the cached
    `load_commodity_history(db_path)`; CONTEXT only, labelled 'research': never a mark, never
    in P&L or delta (hard rule 2), nothing asks Bloomberg (hard rule 8). Never raises.

    `contract_ids`: our canonical ids ('CLZ26 Comdty', 'CUX26 Comdty'), or a dict {our id:
    our root_id} when the root is known (it matters where the research app's Bloomberg root
    is still its 'ZZ' placeholder: the id is then matched on (root, year, month), as for the
    history). With ids alone, the root is found in `config/contracts.csv` by the id's
    Bloomberg root. Also accepted: an LME ticket's 'LME:CA 2026-12-10' (curve-positions' id;
    read from the research app's contract of the prompt month) and an option id
    ('CLZ26C 75 Comdty', which has no figures: its `underlying` future id is given to ask for).

    Returns {our id: {contract_id, root_id (ours), source_contract_id (the research id
    read), research_root, open_interest (lots, the latest on or before `as_of`), oi_date,
    adv (mean daily volume in lots over the last `window` days with a volume on or before
    `as_of`), adv_days (how many it used), window, volume_last, volume_date, lot_size and
    lot_unit (the research contract's lot, which the figures are in), exchange, underlying,
    label 'research', as_of, reason ('' when both figures are there, else what is missing
    and why), note (caveats, '' when none), source_kind and source_note (`research_source`:
    'mock' means the figures are generated)}}. A value not on file is None, with the reason.

    Caveats, in `note` or `reason` where they apply:
      * The research app keeps a contract's rows only while it is among its root's
        `calendar_depth` nearest contracts, so a deferred month beyond that depth has no
        figures (the reason says so).
      * Chinese exchanges (SHFE, DCE, ZCE, INE, GFEX): no factor is applied. The research
        app's own note is that these exchanges counted open interest double-sided until
        January 2020 and single-sided since; whether Bloomberg's figures for a contract are
        single- or double-sided is not verified on a terminal. The dev PC's research data is
        mock, so it cannot settle it.
      * LME: the monthly contract of the prompt month, not the ticket's own prompt; whether
        Bloomberg reports LME volume and open interest per monthly prompt is not verified.
      * The figures are in the research contract's lots; a lot size that differs from ours
        is named.
    """
    return load_commodity_history(db_path).contract_liquidity(contract_ids, as_of, window)


# ----------------------------------------------------------------- price check
PRICE_CHECK_THRESHOLD = 0.20        # futures and LME: research price vs ours
FX_CHECK_THRESHOLD = 0.02           # USDCNH / USDCNY: FX moves far less than commodities
FX_CHECK_PAIRS = ("USDCNH", "USDCNY")
CHINA_CURRENCIES = ("CNY", "CNH")
FX_HEDGE_ROOT = "SGX:XUC"           # the SGX USD/CNH future, quoted CNH per USD
CHECK_KINDS = ("future", "lme", "fx")
_UNIT_FACTORS = (100.0, 1000.0, 10000.0, 0.01, 0.001, 0.0001)


def _factor_text(factor: float) -> str:
    return f"{factor:,.0f}" if factor >= 100 else (f"{factor:.2f}" if factor >= 0.01 else f"{factor:.2g}")


def _positive(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool) and bool(np.isfinite(value))
            and value > 0)


def _latest_mark(conn: sqlite3.Connection, instrument_id: str, mark_type: str, as_of_iso: str):
    """(value, as_of_date) of the latest official mark on or before `as_of`, or (None, None)."""
    row = conn.execute(
        "SELECT value, as_of_date FROM marks_official WHERE instrument_id = ? AND mark_type = ? "
        "AND as_of_date <= ? ORDER BY as_of_date DESC, settle_date DESC LIMIT 1",
        (instrument_id, mark_type, as_of_iso)).fetchone()
    return (None, None) if row is None else (row[0], row[1])


def _book_futures(conn: sqlite3.Connection, as_of_iso: str) -> List[dict]:
    """The futures on file not expired by `as_of` and dealt on or before it: per contract its root,
    quote currency, the |lots|-weighted average fill, the lots, the last trade date, and our latest
    official FUTURE_PX on or before `as_of` (None when none)."""
    rows = conn.execute(
        "SELECT t.instrument_id, i.base_ccy, i.quote_ccy, SUM(ABS(t.quantity) * t.price), SUM(ABS(t.quantity)), "
        "MAX(t.trade_date) FROM trades_official t JOIN instruments i ON i.instrument_id = t.instrument_id "
        "WHERE t.product = 'FUTURE' AND i.expiry_date >= ? AND t.trade_date <= ? "
        "GROUP BY t.instrument_id, i.base_ccy, i.quote_ccy ORDER BY i.base_ccy, t.instrument_id",
        (as_of_iso, as_of_iso)).fetchall()
    out = []
    for cid, root, quote, notional, lots, last_trade in rows:
        mark, mark_date = _latest_mark(conn, cid, "FUTURE_PX", as_of_iso)
        out.append({"contract_id": cid, "root_id": str(root or ""), "quote_ccy": str(quote or "").upper(),
                    "lots": float(lots or 0.0), "fill": float(notional) / float(lots) if lots else None,
                    "fill_date": last_trade, "mark": mark, "mark_date": mark_date})
    return out


def _book_lme(conn: sqlite3.Connection, as_of_iso: str) -> List[dict]:
    """The LME tickets on file whose prompt is on or after `as_of`, dealt on or before it, per metal
    (the root, 'LME:CA'): the |tonnes|-weighted average fill, the tonnes, the prompt of the most
    tonnes, the last trade date, and our latest official cash SPOT on or before `as_of`."""
    rows = conn.execute(
        "SELECT t.instrument_id, l.settle_date, SUM(ABS(t.quantity) * t.price), SUM(ABS(t.quantity)), "
        "MAX(t.trade_date) FROM trades_official t JOIN trade_legs l ON l.trade_id = t.trade_id "
        "AND l.settles_cash = 0 WHERE t.product = 'LME_FWD' AND l.settle_date >= ? AND t.trade_date <= ? "
        "GROUP BY t.instrument_id, l.settle_date ORDER BY t.instrument_id, l.settle_date",
        (as_of_iso, as_of_iso)).fetchall()
    metals: Dict[str, dict] = {}
    for root, prompt, notional, tonnes, last_trade in rows:
        m = metals.setdefault(root, {"contract_id": root, "root_id": root, "notional": 0.0, "lots": 0.0,
                                     "fill_date": None, "prompt": None, "prompt_tonnes": -1.0})
        m["notional"] += float(notional or 0.0)
        m["lots"] += float(tonnes or 0.0)
        m["fill_date"] = max(filter(None, (m["fill_date"], last_trade)), default=None)
        if float(tonnes or 0.0) > m["prompt_tonnes"]:
            m["prompt"], m["prompt_tonnes"] = prompt, float(tonnes or 0.0)
    out = []
    for root, m in metals.items():
        mark, mark_date = _latest_mark(conn, root, "SPOT", as_of_iso)
        out.append({"contract_id": root, "root_id": root, "lots": m["lots"],
                    "fill": m["notional"] / m["lots"] if m["lots"] else None, "fill_date": m["fill_date"],
                    "prompt": m["prompt"], "mark": mark, "mark_date": mark_date})
    return out


def _blank_check(item: dict, kind: str, threshold: float, reason: str = "") -> dict:
    return {"kind": kind, "root_id": item["root_id"], "contract_id": item["contract_id"],
            "research_contract_id": None, "ours": None, "ours_kind": "", "ours_date": None, "research": None,
            "research_date": None, "factor": None, "gap_pct": None, "threshold": float(threshold), "flagged": False,
            "hint": "", "reason": reason, "note": "", "sentence": "", "lots": item["lots"],
            "size_unit": {"future": "lots", "lme": "t", "fx": "lots"}[kind]}


def _set_ours(rec: dict, item: dict, what: str, as_of: pd.Timestamp) -> bool:
    """Our side of the comparison: the official mark, else the average fill. False with the reason set."""
    if _positive(item["mark"]):
        rec.update(ours=float(item["mark"]), ours_kind="mark", ours_date=item["mark_date"])
    elif _positive(item["fill"]):
        rec.update(ours=float(item["fill"]), ours_kind="fill", ours_date=item["fill_date"])
    else:
        rec["reason"] = f"{item['contract_id']}: no official {what} on or before {as_of:%Y-%m-%d} and no positive fill"
        return False
    return True


def _compare(rec: dict) -> dict:
    """Factor, gap and flag once both sides are set, and the row's sentence."""
    if not _positive(rec["research"]):
        rec["reason"] = f"{rec['research_contract_id']}: research price {rec['research']:g} is not a positive price"
        return rec
    factor = rec["research"] / rec["ours"]
    rec["factor"] = factor
    rec["gap_pct"] = (factor - 1.0) * 100.0
    rec["flagged"] = bool(abs(factor - 1.0) > rec["threshold"])
    if rec["flagged"] and rec["kind"] != "fx":
        unit = [k for k in _UNIT_FACTORS if abs(factor / k - 1.0) <= 0.05]
        if unit:
            rec["hint"] = (f"a factor of {_factor_text(unit[0])}: looks like a price-unit difference, "
                           "not a market move")
    return rec


def _sentence(rec: dict) -> str:
    what = {"future": "", "lme": " cash", "fx": ""}[rec["kind"]]
    if rec["factor"] is None:
        return f"{rec['root_id']}{what}: not compared ({rec['reason']})"
    ours = "our official mark" if rec["ours_kind"] == "mark" else "our average fill"
    body = (f"{rec['root_id']}{what}: research {rec['research']:,.6g} ({rec['research_contract_id']}, "
            f"{rec['research_date']}) against {ours} {rec['ours']:,.6g} ({rec['ours_date']}), "
            f"x{_factor_text(rec['factor'])}, {rec['gap_pct']:+.1f} %")
    if not rec["flagged"]:
        return body + f", within {rec['threshold'] * 100:g} %"
    body += f", more than {rec['threshold'] * 100:g} % apart"
    if rec["kind"] == "fx":
        body += (": this rate converts every China trade's P&L history to USD, so the hedge % and VaR of "
                 "every China trade are off by as much")
    elif rec["hint"]:
        body += f" ({rec['hint']})"
    return body


def _check_contract(hist: "CommodityHistory", item: dict, as_of: pd.Timestamp, threshold: float) -> dict:
    """One contract of the book against the research app's latest settle on or before `as_of`."""
    cid, root = item["contract_id"], item["root_id"]
    rec = _blank_check(item, "future", threshold)
    if not _set_ours(rec, item, "FUTURE_PX", as_of):
        return rec
    row, why = hist._root_row(root)
    if row is None:
        rec["reason"] = why
        return rec
    stored = hist._root_prices(root)
    if stored.attrs.get("reason"):
        rec["reason"] = stored.attrs["reason"]
        return rec
    wide = stored.loc[:as_of]
    rid, why = hist.resolve_contract(cid, root)
    series = wide[rid].dropna() if rid is not None and rid in wide.columns else pd.Series(dtype=float)
    if series.empty:
        # The nearest month of the root the research app settled on its latest date.
        latest = wide.dropna(how="all")
        parts = parse_bbg_ticker(cid)
        missing = why or f"no settlement on or before {as_of:%Y-%m-%d}"
        if latest.empty or parts is None:
            rec["reason"] = f"{cid}: {missing}; no research settlement of {root} to compare with"
            return rec
        _, code, digits, _ = parts
        year = int(digits) + 2000 if len(digits) == 2 else hist._one_digit_year(int(digits))
        rid = _nearest_month(hist, latest.iloc[-1].dropna().index, year * 12 + month_from_code(code))
        if rid is None:
            rec["reason"] = f"{cid}: {missing}; no research settlement of {root} to compare with"
            return rec
        series = latest[rid].dropna()
        rec["note"] = (f"{cid}: {missing} in the research app; compared with {rid}, the nearest month it "
                       f"settled on {series.index[-1]:%Y-%m-%d}")
    rec["research_contract_id"] = rid
    rec["research"] = float(series.iloc[-1])
    rec["research_date"] = f"{series.index[-1]:%Y-%m-%d}"
    return _compare(rec)


def _nearest_month(hist: "CommodityHistory", research_ids, target: int, not_before: Optional[int] = None):
    """The research contract among `research_ids` whose (year x 12 + month) is nearest `target`
    (none earlier than `not_before` when given, unless nothing else is left); None when none."""
    c = hist.contracts
    cand = [(int(c.at[x, "year"]) * 12 + int(c.at[x, "month"]), x)
            for x in research_ids if x in c.index and pd.notna(c.at[x, "year"]) and pd.notna(c.at[x, "month"])]
    if not_before is not None and any(k >= not_before for k, _ in cand):
        cand = [(k, x) for k, x in cand if k >= not_before]
    if not cand:
        return None
    return min(cand, key=lambda kx: (abs(kx[0] - target), kx[0], kx[1]))[1]


def _check_lme(hist: "CommodityHistory", item: dict, as_of: pd.Timestamp, threshold: float) -> dict:
    """One LME metal: our official cash price (else the tickets' average fill) against the research
    app's front monthly on its latest date on or before `as_of` (with a fill, the monthly of the
    prompt holding the most tonnes, the nearer comparison for a forward price)."""
    root = item["root_id"]
    rec = _blank_check(item, "lme", threshold)
    if not _set_ours(rec, item, "cash SPOT", as_of):
        return rec
    row, why = hist._root_row(root)
    if row is None:
        rec["reason"] = why
        return rec
    stored = hist._root_prices(root)
    if stored.attrs.get("reason"):
        rec["reason"] = stored.attrs["reason"]
        return rec
    latest = stored.loc[:as_of].dropna(how="all")
    if latest.empty:
        rec["reason"] = f"{root}: no research settlement on or before {as_of:%Y-%m-%d}"
        return rec
    date = latest.index[-1]
    front = date.year * 12 + date.month
    if rec["ours_kind"] == "fill" and item.get("prompt"):
        p = pd.Timestamp(item["prompt"])
        target, how = p.year * 12 + p.month, f"the monthly of the {item['prompt']} prompt (compared with the fill)"
    else:
        target, how = front, "the front monthly (compared with the cash price)"
    rid = _nearest_month(hist, latest.iloc[-1].dropna().index, target, not_before=front)
    if rid is None:
        rec["reason"] = f"{root}: no research monthly settled on {date:%Y-%m-%d}"
        return rec
    rec.update(research_contract_id=rid, research=float(latest.at[date, rid]), research_date=f"{date:%Y-%m-%d}")
    rec["note"] = f"research {rid}: {how}"
    return _compare(rec)


def _check_fx(hist: "CommodityHistory", conn: sqlite3.Connection, pair: str, futures: List[dict],
              as_of: pd.Timestamp, threshold: float) -> dict:
    """One China pair: the research app's fx_daily rate against our official SPOT of the pair (else
    of USDCNH, else the SGX USD/CNH future's latest official price, else its average fill; all CNH
    per USD)."""
    iso = f"{as_of:%Y-%m-%d}"
    xuc = [f for f in futures if f["root_id"] == FX_HEDGE_ROOT]
    item = {"root_id": pair, "contract_id": pair, "lots": float(sum(f["lots"] for f in xuc))}
    rec = _blank_check(item, "fx", threshold)
    notes = []
    for inst in dict.fromkeys((pair, "USDCNH")):
        value, date = _latest_mark(conn, inst, "SPOT", iso)
        if _positive(value):
            rec.update(ours=float(value), ours_kind="mark", ours_date=date)
            if inst != pair:
                notes.append(f"no official {pair} SPOT: compared with our USDCNH spot (onshore and offshore "
                             "rates normally sit a fraction of a percent apart)")
            break
    if rec["ours"] is None and xuc:
        marked = sorted((f for f in xuc if _positive(f["mark"])), key=lambda f: (-f["lots"], f["contract_id"]))
        filled = sum(f["lots"] for f in xuc if _positive(f["fill"]))
        if marked:
            rec.update(ours=float(marked[0]["mark"]), ours_kind="mark", ours_date=marked[0]["mark_date"])
            notes.append(f"no official USDCNH SPOT: compared with our official price of the SGX USD/CNH future "
                         f"{marked[0]['contract_id']} (a forward, a little off spot)")
        elif filled:
            rec.update(ours=sum(f["fill"] * f["lots"] for f in xuc if _positive(f["fill"])) / filled,
                       ours_kind="fill", ours_date=max(f["fill_date"] for f in xuc if f["fill_date"]))
            notes.append("no official USDCNH SPOT and no mark of the SGX USD/CNH future: compared with the "
                         "future's average fill (a forward, a little off spot)")
    if rec["ours"] is None:
        rec["reason"] = (f"{pair}: no official USDCNH SPOT on or before {iso} and no SGX USD/CNH future "
                         "on file to compare with")
        rec["note"] = "; ".join(notes)
        return rec
    series = hist.fx_series(pair)
    if series.attrs.get("reason"):
        rec["reason"] = series.attrs["reason"]
        rec["note"] = "; ".join(notes)
        return rec
    series = series.loc[:as_of]
    if series.empty:
        rec["reason"] = f"{pair}: no research rate on or before {iso}"
        rec["note"] = "; ".join(notes)
        return rec
    rec.update(research_contract_id=pair, research=float(series.iloc[-1]), research_date=f"{series.index[-1]:%Y-%m-%d}")
    rec["note"] = "; ".join(notes)
    return _compare(rec)


def research_price_check(conn: sqlite3.Connection, as_of=None, threshold: float = PRICE_CHECK_THRESHOLD,
                         db_path: Union[str, Path, None] = None, fx_threshold: float = FX_CHECK_THRESHOLD) -> dict:
    """Whether the research app's prices are the same market as ours, one row per check:
      * kind 'future': each root of the book's open futures (product FUTURE, not expired on
        `as_of`), our latest official FUTURE_PX on or before `as_of` (with no mark, the
        |lots|-weighted average fill of the trades on file) against the research app's latest raw
        settle of the same contract on or before `as_of` (the unit of our FUTURE_PX marks; with
        that contract not in the research app, its nearest month, said in `note`); per root the
        contract shown is the one with the most lots that could be compared;
      * kind 'lme': each LME metal with an open ticket (LME_FWD whose prompt is on or after
        `as_of`), our latest official cash SPOT (else the tickets' |tonnes|-weighted average fill)
        against the research app's front monthly on its latest date (with a fill, the monthly of
        the prompt holding the most tonnes); LME futures (the ferrous contracts) are 'future' rows;
      * kind 'fx': with any China exposure (an open future in CNY / CNH, or the SGX USD/CNH
        future), the research app's USDCNH and, where it has it, USDCNY (`fx_daily`) against our
        official SPOT of the pair, else of USDCNH, else the SGX USD/CNH future's official price,
        else its average fill (CNH per USD). This rate converts every China trade's history, so
        a gap here moves the hedge % and VaR of every China trade.
    A row is flagged when the research price is more than its threshold away from ours:
    `threshold` (20 %) for futures and LME, `fx_threshold` (2 %) for FX.

    `conn` is our risk database (read: trades_official, trade_legs, instruments,
    marks_official). Pure reads, CONTEXT only: nothing is written anywhere, nothing here is a
    mark or enters P&L or delta (hard rule 2), and nothing asks Bloomberg (hard rule 8). Options
    on futures are not checked. Never raises.

    Returns {available, path, as_of, threshold, fx_threshold, label 'research', source_kind,
    source_note (`research_source`), reason ('' or why nothing could be checked), sentence (one
    line for the Risk tab, counting every kind), rows, flagged}:
      rows     flagged first, then kind (future, lme, fx), then root: {kind, root_id (the pair for
               fx), contract_id (ours; the metal for lme, the pair for fx), research_contract_id
               (the research contract, or the pair), ours, ours_kind 'mark' | 'fill' | '',
               ours_date, research (raw settle or rate), research_date, factor (research / ours),
               gap_pct ((factor - 1) x 100), threshold, flagged (bool), hint ('' or a price-unit
               reading of the factor), reason ('' when compared, else why not), note ('' or what
               was substituted), sentence (the row in words), lots, size_unit ('lots' | 't'),
               contracts (future rows: every contract of the root, the same fields)};
      flagged  the rows with flagged True."""
    hist = load_commodity_history(db_path)
    out = {"available": bool(hist.available), "path": hist.path, "as_of": None, "threshold": float(threshold),
           "fx_threshold": float(fx_threshold), "label": LABEL, "source_kind": hist.source_kind,
           "source_note": hist.source_note, "reason": "", "sentence": "", "rows": [], "flagged": []}

    def stop(reason: str) -> dict:
        out["reason"] = reason
        out["sentence"] = f"research prices not checked: {reason}"
        return out

    try:
        as_of_ts = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.today().normalize()
    except (TypeError, ValueError) as exc:
        return stop(f"as-of {as_of!r} is not a date ({exc})")
    if pd.isna(as_of_ts):
        return stop(f"as-of {as_of!r} is not a date")
    out["as_of"] = iso = f"{as_of_ts:%Y-%m-%d}"
    if not hist.available:
        return stop(hist.reason)
    try:
        futures = _book_futures(conn, iso)
        metals = _book_lme(conn, iso)
    except Exception as exc:  # noqa: BLE001 -- a book that will not read is a reason, never a crash
        return stop(f"the book could not be read ({type(exc).__name__}: {exc})")
    china = any(f["quote_ccy"] in CHINA_CURRENCIES or f["root_id"] == FX_HEDGE_ROOT for f in futures)
    if not futures and not metals:
        return stop(f"no open futures or LME tickets on {iso}")
    hist.prefetch_roots({i["root_id"] for i in futures + metals if i["root_id"]})
    rows = []
    by_root: Dict[str, List[dict]] = {}
    for item in futures:
        try:
            rec = _check_contract(hist, item, as_of_ts, threshold)
        except Exception as exc:  # noqa: BLE001 -- one contract's failure is its reason
            rec = _blank_check(item, "future", threshold,
                               f"{item['contract_id']} could not be checked ({type(exc).__name__}: {exc})")
        by_root.setdefault(item["root_id"], []).append(rec)
    for recs in by_root.values():
        order = sorted(recs, key=lambda r: (r["factor"] is None, -r["lots"], r["contract_id"]))
        pick = dict(order[0])
        pick["contracts"] = [dict(r) for r in recs]
        rows.append(pick)
    for item in metals:
        try:
            rows.append(_check_lme(hist, item, as_of_ts, threshold))
        except Exception as exc:  # noqa: BLE001 -- one metal's failure is its reason
            rows.append(_blank_check(item, "lme", threshold,
                                     f"{item['root_id']} could not be checked ({type(exc).__name__}: {exc})"))
    if china:
        for pair in FX_CHECK_PAIRS:
            if pair != "USDCNH" and pair not in hist.fx.columns:
                continue
            try:
                rows.append(_check_fx(hist, conn, pair, futures, as_of_ts, fx_threshold))
            except Exception as exc:  # noqa: BLE001 -- one pair's failure is its reason
                rows.append(_blank_check({"root_id": pair, "contract_id": pair, "lots": 0.0}, "fx", fx_threshold,
                                         f"{pair} could not be checked ({type(exc).__name__}: {exc})"))
    for r in rows:
        r["sentence"] = _sentence(r)
        for c in r.get("contracts", []):
            c["sentence"] = _sentence(c)
    rows.sort(key=lambda r: (not r["flagged"], CHECK_KINDS.index(r["kind"]), r["root_id"]))
    out["rows"] = rows
    out["flagged"] = [r for r in rows if r["flagged"]]
    n_cmp = sum(1 for r in rows if r["factor"] is not None)
    n_flag = len(out["flagged"])
    limits = f"{threshold * 100:g} % for futures and LME, {fx_threshold * 100:g} % for FX"
    if n_flag:
        worst = sorted(out["flagged"], key=lambda r: -abs(float(np.log(r["factor"]))))[:3]
        text = (f"{n_flag} of {n_cmp} prices compared are further from ours than allowed ({limits}): "
                + ", ".join(f"{r['root_id']} x{_factor_text(r['factor'])}" for r in worst))
    elif n_cmp:
        text = f"all {n_cmp} prices compared are within {limits} of ours"
    else:
        text = "no price could be compared"
    if len(rows) > n_cmp:
        text += f"; {len(rows) - n_cmp} not compared"
    fx_flagged = [r["root_id"] for r in out["flagged"] if r["kind"] == "fx"]
    if fx_flagged:
        text += (f"; {' and '.join(fx_flagged)} off: it converts every China trade's history, so their hedge % "
                 "and VaR are off too")
    if hist.source_kind != "real":
        text += f"; {hist.source_note}"
    out["sentence"] = text
    return out


# ------------------------------------------------------------------ light reads
_SQL_CHUNK = 900   # bound parameters per query, under SQLite's lowest default limit


def _db_path(db_path: Union[str, Path, None]) -> Optional[Path]:
    seen = [Path(db_path).expanduser()] if db_path is not None else [Path(c["path"]) for c in candidates()]
    found = [p for p in seen if p.is_file()]
    return found[0] if found else None


def latest_settles(contract_ids, on_or_before, db_path: Union[str, Path, None] = None) -> Dict[str, Tuple[str, float]]:
    """Each contract's latest research settlement on or before `on_or_before`, read straight from
    `price_daily` by its primary key, with no history load and no contract lookup built: one query
    per 900 ids (plus one per id matched on its month, below). For a reader that needs a level or
    two, such as a calendar's roll-down, instead of each root's whole history.

    `contract_ids`: the research app's ids ('CLZ26 Comdty'; our canonical ids are the same form),
    or a dict {id: our root_id}: with the root, an id that is not in the research app as it is (its
    Bloomberg root is still the research app's 'ZZ' placeholder) is matched on (root, contract
    year, month), the rule of `CommodityHistory.resolve_contract`, and an id found under another
    root is left out, as there. Two-digit years only for that match.

    Returns {id as given: (date ISO, settle)}, settle RAW (Bloomberg's quoted price as stored, the
    unit of our FUTURE_PX marks; x price_scale for quote units). An id with no settlement on or
    before the date, not in the research app, or anything unreadable is left out: never raises.
    Research CONTEXT only: never a mark, never in P&L or delta (hard rule 2), nothing asked of
    Bloomberg (hard rule 8). Whether the prices are Bloomberg's or mock: `research_source`."""
    try:
        day = f"{pd.Timestamp(on_or_before):%Y-%m-%d}"
    except (TypeError, ValueError):
        return {}
    if day == "NaT":
        return {}
    roots = ({str(k): (str(v) if v else None) for k, v in contract_ids.items()} if isinstance(contract_ids, dict)
             else {str(k): None for k in contract_ids})
    db = _db_path(db_path)
    if db is None or not roots:
        return {}
    asked: Dict[str, List[str]] = {}                     # the research id form -> the ids as given
    for k in roots:
        asked.setdefault(" ".join(k.split()), []).append(k)
    out: Dict[str, Tuple[str, float]] = {}
    sql = ("SELECT p.contract_id, c.instrument_id, MAX(p.date), p.settle FROM price_daily p "
           "JOIN contract c ON c.contract_id = p.contract_id WHERE p.contract_id IN ({}) AND p.date <= ? "
           "GROUP BY p.contract_id")
    try:
        conn = _connect(db)
        try:
            found = {}
            ids = list(asked)
            for i in range(0, len(ids), _SQL_CHUNK):
                part = ids[i:i + _SQL_CHUNK]
                for cid, inst, date, settle in conn.execute(sql.format(",".join("?" * len(part))), (*part, day)):
                    found[cid] = (inst, date, settle)
            for rid, given in ((rid, g) for rid, gs in asked.items() for g in gs):
                root = roots[given]
                root_key = _norm(root).replace(" ", "") if root else None
                hit = found.get(rid)
                if hit is not None:
                    if root_key is None or hit[0] == root_key:
                        out[given] = (hit[1], float(hit[2]))
                    continue
                if root_key is None:
                    continue
                exists = conn.execute("SELECT 1 FROM contract WHERE contract_id = ?", (rid,)).fetchone()
                parts = parse_bbg_ticker(rid)
                if exists is not None or parts is None or len(parts[2]) != 2:
                    continue
                twin = conn.execute("SELECT contract_id FROM contract WHERE instrument_id = ? AND year = ? AND month = ?",
                                    (root_key, int(parts[2]) + 2000, month_from_code(parts[1]))).fetchone()
                if twin is None:
                    continue
                row = conn.execute("SELECT date, settle FROM price_daily WHERE contract_id = ? AND date <= ? "
                                   "ORDER BY date DESC LIMIT 1", (twin[0], day)).fetchone()
                if row is not None:
                    out[given] = (row[0], float(row[1]))
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 -- an unreadable database gives nothing, never a crash
        return {}
    return out


def _book_roots(book_db: Union[str, Path]) -> List[str]:
    """The contract roots ('NYMEX:CL', 'LME:CA') of every trade on file in our risk database."""
    uri = f"{Path(book_db).resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=CONNECT_TIMEOUT_SECONDS)
    try:
        rows = conn.execute("SELECT DISTINCT i.base_ccy FROM trades t JOIN instruments i "
                            "ON i.instrument_id = t.instrument_id WHERE i.base_ccy LIKE '%:%'").fetchall()
    finally:
        conn.close()
    return sorted(r[0] for r in rows if r[0])


def warm(root_ids=None, db_path: Union[str, Path, None] = None, book_db: Union[str, Path, None] = None) -> dict:
    """Preload what the first Risk, Book or P&L screen would otherwise read cold: the history
    (`load_commodity_history`), its provenance (`research_source`), the contract lookups
    `resolve_contract` builds on first use, and the settlements of `root_ids` (or, with
    `book_db`, our risk database, of every root with a trade on file) in one batched read. Meant
    for a background thread at start-up (`threading.Thread(target=warm, kwargs=..., daemon=True)`);
    a request arriving meanwhile waits for the same read rather than repeating it. Read-only on
    both databases; never raises.

    Returns {ok, seconds: {load, source, lookup, roots}, roots (the roots asked), reason}."""
    out = {"ok": False, "seconds": {}, "roots": [], "reason": ""}
    try:
        t = time.perf_counter()
        hist = load_commodity_history(db_path)
        out["seconds"]["load"] = round(time.perf_counter() - t, 3)
        t = time.perf_counter()
        research_source(db_path)
        out["seconds"]["source"] = round(time.perf_counter() - t, 3)
        if not hist.available:
            out["reason"] = hist.reason
            return out
        t = time.perf_counter()
        hist.resolve_contract("")        # builds the id lookup
        hist._by_month()
        out["seconds"]["lookup"] = round(time.perf_counter() - t, 3)
        roots = list(root_ids) if root_ids is not None else (_book_roots(book_db) if book_db is not None else [])
        out["roots"] = sorted({str(r) for r in roots if r})
        t = time.perf_counter()
        if out["roots"]:
            hist.prefetch_roots(out["roots"])
        out["seconds"]["roots"] = round(time.perf_counter() - t, 3)
        out["ok"] = True
    except Exception as exc:  # noqa: BLE001 -- a warm-up that fails leaves the cold path as it was
        out["reason"] = f"warm-up stopped ({type(exc).__name__}: {exc})"
    return out
