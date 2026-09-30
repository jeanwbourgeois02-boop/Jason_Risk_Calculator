"""Daily price history for the Risk metrics, read from this app's own `price_history` table.

bbg-backfill fills `price_history` from Bloomberg's daily PX_LAST (with PX_VOLUME and OPEN_INT)
when the user presses "Pull Bloomberg now", and the marks snapshot carries it to other PCs (user
decision 2026-09-30: the app no longer reads the research app's database). Its ids:

  * a future's canonical contract id ('CLZ26 Comdty'): every contract of each held root's chain
    over about two years, expired ones included (`data.bloomberg.library.risk_history_needs`);
  * an FX pair as stored ('USDCNH', 'EURUSD', 'XAUUSD'; CNY and CNH both through USDCNH);
  * an LME metal's two pillars, 'LME:CA CASH' and 'LME:CA 3M'.

`settle` is Bloomberg's quoted price, the unit of our FUTURE_PX marks; our `multiplier` is per
1.0 of it, so a P&L change is the settle change x multiplier x lots, and `settle_series` gives quote units
(settle x the root's `price_scale`).

What is served (on `CommodityHistory`, from `load_commodity_history(db)`):

  * `settle_series(contract_id)`: the contract's closes in quote units.
  * `constant_maturity_series(root_id, months_ahead)`: on each date the close of the
    `months_ahead`-th listed contract (1 = the front; listed = last trade date after the date),
    rolling ON a contract's last trade date; `constant_maturity_changes` is its day-on-day change
    taken on one contract, so a roll never shows as a jump.
  * `fx_series(pair)` and `usd_per_unit(currency)`.
  * `daily_pnl_series_for_position(...)`: the USD P&L a held position would have made each day:
    the contract's own changes while it has closes, before them the constant-maturity changes at
    the rank it holds on the as-of date; a non-USD contract converts at that day's USD per quote
    unit (the last rate within `FX_TOLERANCE_DAYS`).
  * `window_move` / `window_move_detail`: one contract's fractional move over a window, no roll
    (the commodity stress's historical replays).
  * `contract_liquidity`: latest open interest and average daily volume per contract.
  * LME: a prompt 'LME:CA 2026-12-16' is read off the cash and 3M closes, linear in time between
    that day's cash date and 3M date, the cash close before the cash date and the 3M close flat
    beyond the 3M date. `contracts` lists one such id per monthly prompt (the third Wednesday),
    so a prompt month resolves like a futures month.

A risk input only: nothing here is written to `marks`, used as a mark, or used for P&L or delta
(hard rule 2), and nothing asks Bloomberg (hard rule 8). The database is opened read-only, one
short connection per read.

Where: `load_commodity_history(db)` takes the book database's path or an open sqlite3
connection to it (an in-memory one is copied at once); None = `data.paths.get_db_path()`, the
app's default database. Cached per (path, the file's mtime_ns and size, config/contracts.csv's
stamp): the app runs SQLite in journal_mode=delete, so any write moves the file's stamp. With no
rows, `available` is False with `NO_HISTORY` as its reason and every series is empty with that
reason in `attrs['reason']`: never an exception.
"""
from __future__ import annotations

import bisect
import datetime as dt
import re
import sqlite3
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

from data.contracts.tickers import make_contract_id, month_code, month_from_code, padded_root, parse_bbg_ticker, \
    parse_option_ticker
from data.contracts.months import estimated_last_trade_date
from data.contracts.universe import CONTRACTS_CSV, load_roots

__all__ = ["CommodityHistory", "ContractMap", "DEFAULT_FX_PAIR", "FX_TOLERANCE_DAYS", "LABEL", "LIQUIDITY_WINDOW",
           "NO_HISTORY", "TABLE", "contract_liquidity", "load_commodity_history", "warm", "window_move"]

LABEL = "Bloomberg history"
TABLE = "price_history"
NO_HISTORY = "No price history yet: press Pull Bloomberg now (it fetches two years of daily closes once)"
SOURCE_KIND = "bloomberg"
CONNECT_TIMEOUT_SECONDS = 5.0
FX_TOLERANCE_DAYS = 7
# The liquidity check (`contract_liquidity`): days in the volume average, and how old the
# latest open interest may be before the note says so.
LIQUIDITY_WINDOW = 20
LIQUIDITY_STALE_DAYS = 7
# How far past the last close the contract list reaches: a held contract with no close of its
# own (Bloomberg returned none) still has its place on the strip.
LIST_MONTHS_AHEAD = 36
CHINA_EXCHANGES = ("SHFE", "DCE", "ZCE", "INE", "GFEX")
_CHINA_NOTE = ("{exchange}: open interest and volume as Bloomberg gives them, no factor applied; the Chinese "
               "exchanges counted open interest double-sided until January 2020 and single-sided since, and "
               "whether Bloomberg's figures for this contract are single- or double-sided is not verified")
_LME_ID_RE = re.compile(r"^(?P<root>LME:[A-Z0-9]+)\s+(?P<prompt>\d{4}-\d{2}-\d{2})$")
_PILLAR_RE = re.compile(r"^(?P<root>LME:[A-Z0-9]+) (?P<pillar>CASH|3M)$")
_FX_RE = re.compile(r"^[A-Z]{6}$")
_SQL_CHUNK = 900   # bound parameters per query, under SQLite's lowest default limit

# The pair each currency converts through when the caller names none: CNY and CNH through USDCNH.
DEFAULT_FX_PAIR = {"CNY": "USDCNH", "CNH": "USDCNH"}


def _our_root_of(contract_id: str) -> Optional[str]:
    """Our root id for a canonical contract id, by its Bloomberg root in `config/contracts.csv`;
    None when no root or more than one has it."""
    parts = parse_bbg_ticker(contract_id)
    if parts is None:
        return None
    try:
        roots = load_roots()
    except Exception:  # noqa: BLE001 -- no universe: no root
        return None
    hits = [r.root_id for r in roots.values()
            if r.bbg_root.strip().upper() == parts[0] and r.bbg_yellow_key.upper() == parts[3].upper()]
    return hits[0] if len(hits) == 1 else None


class ContractMap(Mapping):
    """The date -> contract map a constant-maturity series carries in `attrs['contracts']`,
    read-only. pandas deep-copies a Series' attrs on every operation, so this map is immutable
    (a deep copy returns it as it is) and its dict is built only when first read."""
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
    """A kept series handed out with its own attrs; the values a shallow (copy-on-write) copy."""
    out = s.copy(deep=False)
    out.attrs = {k: (dict(v) if isinstance(v, dict) else v) for k, v in s.attrs.items()}
    return out


def _connect(path: Path) -> sqlite3.Connection:
    """A read-only connection (`mode=ro`): any write through it raises. Close it at once."""
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=CONNECT_TIMEOUT_SECONDS)


def _norm(text: str) -> str:
    return " ".join(str(text or "").split()).upper()


def _source() -> dict:
    """What `status()` says of who wrote the rows: always Bloomberg (kept for old consumers)."""
    return {"source_kind": SOURCE_KIND, "source_note": "", "source_providers": [], "source_jobs": []}


# ------------------------------------------------------------------ LME dates
_LME_DATES: Dict[int, Tuple[int, int]] = {}


def _lme_dates(index: pd.DatetimeIndex) -> Tuple[np.ndarray, np.ndarray]:
    """(cash date, 3M date) as ordinals for each close date, `engine.lme`'s own rules."""
    from engine.lme import cash_date, three_month_date
    cash, three = np.empty(len(index), dtype=float), np.empty(len(index), dtype=float)
    for i, ts in enumerate(index):
        day = ts.date()
        hit = _LME_DATES.get(day.toordinal())
        if hit is None:
            hit = (cash_date(day).toordinal(), three_month_date(day).toordinal())
            _LME_DATES[day.toordinal()] = hit
        cash[i], three[i] = hit
    return cash, three


@dataclass
class CommodityHistory:
    """The price history of one book database. `status()` is the JSON-friendly summary."""
    available: bool
    path: str
    reason: str = ""
    last_date: Optional[str] = None
    first_date: Optional[str] = None
    instruments: pd.DataFrame = field(default_factory=pd.DataFrame)   # index root_id
    contracts: pd.DataFrame = field(default_factory=pd.DataFrame)     # index contract_id
    fx: pd.DataFrame = field(default_factory=pd.DataFrame)            # date x pair, rate as stored
    note: str = ""
    candidates: List[dict] = field(default_factory=list)              # [{path, exists}]
    source: dict = field(default_factory=_source)                     # always Bloomberg
    unknown_ids: List[str] = field(default_factory=list)              # ids on file no root claims
    _ids: Dict[str, List[str]] = field(default_factory=dict, repr=False)       # root -> ids with rows
    _lme: Dict[str, Tuple[str, str]] = field(default_factory=dict, repr=False)  # LME root -> (cash id, 3M id)
    _roots: Dict[str, pd.DataFrame] = field(default_factory=dict, repr=False)
    _lookup: Dict[str, dict] = field(default_factory=dict, repr=False)
    _cm: Dict[tuple, pd.DataFrame] = field(default_factory=dict, repr=False)   # (root, months_ahead) -> frame
    _pnl: Dict[tuple, pd.Series] = field(default_factory=dict, repr=False)     # position P&L per argument set
    _own: Optional[sqlite3.Connection] = field(default=None, repr=False)       # private copy of an in-memory db
    _read_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    # ----------------------------------------------------------------- summary
    def status(self) -> dict:
        return {"available": self.available, "path": self.path, "reason": self.reason,
                "first_date": self.first_date, "last_date": self.last_date, "note": self.note,
                "candidates": [dict(c) for c in self.candidates],
                "roots": int(len(self.instruments)), "contracts": int(len(self.contracts)),
                "fx_pairs": [str(c) for c in self.fx.columns],
                "source_kind": self.source["source_kind"], "source_note": self.source["source_note"],
                "source_providers": [], "source_jobs": []}

    @property
    def source_kind(self) -> str:
        """Always 'bloomberg' (kept for old consumers)."""
        return self.source["source_kind"]

    @property
    def source_note(self) -> str:
        """Always '' (kept for old consumers)."""
        return self.source["source_note"]

    # ------------------------------------------------------------------- reads
    def _read(self, sql: str, params: tuple = ()) -> pd.DataFrame:
        if self._own is not None:
            with self._read_lock:
                return pd.read_sql_query(sql, self._own, params=params)
        conn = _connect(Path(self.path))
        try:
            return pd.read_sql_query(sql, conn, params=params)
        finally:
            conn.close()

    def _read_ids(self, ids: List[str], cols: str = "settle", until: Optional[str] = None) -> pd.DataFrame:
        """Rows (contract_id, date, <cols>) of `ids`, in chunks under SQLite's parameter limit."""
        parts = []
        for i in range(0, len(ids), _SQL_CHUNK):
            chunk = ids[i:i + _SQL_CHUNK]
            where = "instrument_id IN (" + ",".join("?" * len(chunk)) + ")"
            params: tuple = tuple(chunk)
            if until is not None:
                where += " AND as_of_date <= ?"
                params += (until,)
            parts.append(self._read(f"SELECT instrument_id AS contract_id, as_of_date AS date, {cols} FROM {TABLE} "
                                    f"WHERE {where} ORDER BY instrument_id, as_of_date", params))
        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["contract_id", "date"])

    # ------------------------------------------------------------------ lookups
    def _root_row(self, root_id: str) -> Tuple[Optional[pd.Series], str]:
        if not self.available:
            return None, self.reason
        key = _norm(root_id).replace(" ", "")
        if key not in self.instruments.index:
            return None, f"no price history on file for {root_id} (Pull Bloomberg now fetches it for the roots held)"
        return self.instruments.loc[key], ""

    def _root_of(self, contract_id: str) -> str:
        """The root of a resolved id (a listed contract or an LME prompt id)."""
        m = _LME_ID_RE.match(contract_id)
        if m:
            return m.group("root")
        return str(self.contracts.at[contract_id, "instrument_id"])

    def resolve_contract(self, contract_id: str, root_id: Optional[str] = None) -> Tuple[Optional[str], str]:
        """(the id as listed here, '') or (None, reason). Our canonical ids are the ids on file, so
        this is the identity for them; an LME prompt 'LME:CA 2026-12-10' resolves when the metal's
        pillars are on file; a one-digit year ('CLZ6 Comdty') is read on the root's strip."""
        if not self.available:
            return None, self.reason
        if "ids" not in self._lookup:
            self._lookup["ids"] = {_norm(c): c for c in self.contracts.index}
        text = " ".join(str(contract_id or "").split())
        found = self._lookup["ids"].get(_norm(text))
        root_key = _norm(root_id).replace(" ", "") if root_id else None
        if found is not None:
            if root_key and self.contracts.at[found, "instrument_id"] != root_key:
                return None, (f"contract {contract_id} belongs to {self.contracts.at[found, 'instrument_id']}, "
                              f"not {root_id}")
            return found, ""
        lme = _LME_ID_RE.match(text.upper())
        if lme:
            root = lme.group("root")
            if root_key and root != root_key:
                return None, f"LME prompt {contract_id} is not a {root_id} prompt"
            if root not in self._lme:
                return None, f"no cash and 3M price history on file for {root}"
            try:
                prompt = dt.date.fromisoformat(lme.group("prompt"))
            except ValueError:
                return None, f"LME prompt {contract_id!r} is not a date"
            return f"{root} {prompt.isoformat()}", ""
        parts = parse_bbg_ticker(text)
        root = root_key or _our_root_of(text)
        if parts is None or not root:
            return None, f"no price history on file for {contract_id}"
        _, code, digits, _ = parts
        try:
            month = month_from_code(code)
        except (KeyError, ValueError):
            return None, f"no price history on file for {contract_id}"
        year = int(digits) + 2000 if len(digits) == 2 else self._one_digit_year(int(digits))
        hit = self._by_month().get((root, year, month))
        if hit is None:
            return None, f"no price history on file for {contract_id} ({root} {code}{year})"
        return hit, ""

    def _by_month(self) -> Dict[tuple, str]:
        """(root, year, month) -> the first contract id with them in `contracts` order."""
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
        """date x contract_id of raw closes from long rows (contract_id, date, settle), `date` a
        datetime. (instrument_id, as_of_date) is the table's key, so no cell has two rows."""
        if df.empty:
            wide = pd.DataFrame(index=pd.DatetimeIndex([], name="date"))
        else:
            wide = df.pivot(index="date", columns="contract_id", values="settle").sort_index()
            wide.index.name = "date"
        return wide.astype(float)

    def prefetch_roots(self, root_ids) -> None:
        """Read the closes of several roots in one pass into the per-root store, so a later call
        per root is served from memory. Roots already read or with no rows are left alone; a
        failed read is not kept and raises nothing."""
        if not self.available:
            return
        with self._read_lock:
            wanted = sorted({_norm(r).replace(" ", "") for r in root_ids} - set(self._roots))
            wanted = [r for r in wanted if r in self._ids]
            if not wanted:
                return
            ids = [i for r in wanted for i in self._ids[r]]
            try:
                df = self._read_ids(ids)
            except Exception:  # noqa: BLE001 -- the per-root read will give the reason
                return
            if not df.empty:
                df["date"] = pd.to_datetime(df["date"])
            owner = {i: r for r in wanted for i in self._ids[r]}
            df["root"] = df["contract_id"].map(owner)
            parts = {root: part.drop(columns="root") for root, part in df.groupby("root", sort=False)}
            for root in wanted:
                self._roots[root] = self._wide(parts.get(root, df.iloc[0:0].drop(columns="root")))

    def _root_prices(self, root_id: str) -> pd.DataFrame:
        """date x id of raw closes for one root, memoised. A failed read gives an empty frame
        with `attrs['reason']`, not memoised."""
        hit = self._roots.get(root_id)
        if hit is not None:
            return hit
        with self._read_lock:
            if root_id not in self._roots:
                ids = self._ids.get(root_id, [])
                try:
                    df = self._read_ids(ids)
                except Exception as exc:  # noqa: BLE001 -- a failed read is a reason, never a crash
                    wide = pd.DataFrame(index=pd.DatetimeIndex([], name="date"))
                    wide.attrs["reason"] = f"{self.path} could not be read ({type(exc).__name__}: {exc})"
                    return wide
                if not df.empty:
                    df["date"] = pd.to_datetime(df["date"])
                self._roots[root_id] = self._wide(df)
            return self._roots[root_id]

    def _lme_raw(self, root: str, prompt: dt.date) -> pd.Series:
        """The raw close of an LME prompt on each day: linear in time between that day's cash date
        (the cash close) and 3M date (the 3M close); cash before, 3M flat beyond. Memoised."""
        memo = self._lookup.setdefault("lme", {})
        key = (root, prompt)
        if key in memo:
            return memo[key]
        cash_id, three_id = self._lme[root]
        wide = self._root_prices(root)
        if wide.empty:
            return _empty(wide.attrs.get("reason") or f"no cash or 3M closes on file for {root}")
        cash = wide[cash_id].to_numpy(dtype=float) if cash_id in wide.columns else np.full(len(wide), np.nan)
        three = wide[three_id].to_numpy(dtype=float) if three_id in wide.columns else np.full(len(wide), np.nan)
        c, m = _lme_dates(wide.index)
        p = float(prompt.toordinal())
        span = np.where(m > c, m - c, 1.0)
        w = np.clip((p - c) / span, 0.0, 1.0)
        value = np.where(w <= 0.0, cash, np.where(w >= 1.0, three, cash + w * (three - cash)))
        out = pd.Series(value, index=wide.index, name=f"{root} {prompt.isoformat()}").dropna()
        out.attrs["reason"] = "" if len(out) else f"no cash or 3M close on file for {root} to read {prompt} from"
        if "could not be read" not in out.attrs["reason"]:
            memo[key] = out
        return out

    def _lme_note(self, rid: str) -> str:
        return f"LME prompt {rid.split(' ', 1)[1]}: interpolated in time between the cash and 3M closes"

    def _raw_settles(self, rid: str) -> pd.Series:
        lme = _LME_ID_RE.match(rid)
        if lme:
            return self._lme_raw(lme.group("root"), dt.date.fromisoformat(lme.group("prompt")))
        root = self._root_of(rid)
        wide = self._root_prices(root)
        if rid not in wide.columns:
            return _empty(wide.attrs.get("reason") or f"contract {rid} has no close of its own on file")
        return wide[rid].dropna()

    def settle_series(self, contract_id: str, root_id: Optional[str] = None) -> pd.Series:
        """The contract's daily closes in quote units (raw x the root's price_scale). Empty with
        `attrs['reason']` when there is none."""
        rid, why = self.resolve_contract(contract_id, root_id)
        if rid is None:
            return _empty(why, contract_id)
        scale = float(self.instruments.at[self._root_of(rid), "price_scale"])
        raw = self._raw_settles(rid)
        if raw.empty:
            return _empty(raw.attrs.get("reason") or f"contract {contract_id} has no closes", contract_id)
        out = (raw * scale).rename(contract_id)
        out.attrs.update(reason="", research_contract_id=rid, price_scale=scale)
        if _LME_ID_RE.match(rid):
            out.attrs["note"] = self._lme_note(rid)
        return out

    # ------------------------------------------------------- constant maturity
    def _strip(self, root: str) -> Tuple[List[str], List[str]]:
        """The root's contracts in last-trade order and their last trade dates (ISO)."""
        strips = self._lookup.setdefault("strips", {})
        if root not in strips:
            c = self.contracts[self.contracts["instrument_id"] == root]
            c = c.sort_values(["last_trade_date", "year", "month"])
            strips[root] = (list(c.index), list(c["last_trade_date"]))
        return strips[root]

    def _ranked(self, root: str, months_ahead: int, dates: pd.DatetimeIndex) -> List[Optional[str]]:
        """For each date, the `months_ahead`-th contract whose last trade date is after it."""
        ids, ltds = self._strip(root)
        out: List[Optional[str]] = []
        for d in dates.strftime("%Y-%m-%d"):
            i = bisect.bisect_right(ltds, d) + months_ahead - 1
            out.append(ids[i] if i < len(ids) else None)
        return out

    def _cm_frame(self, root_id: str, months_ahead: int, start=None) -> Tuple[Optional[pd.DataFrame], str]:
        """The constant-maturity frame (date x contract_id, raw, raw_change, settle, change), built
        once per (root, months_ahead). Callers never modify it."""
        row, why = self._root_row(root_id)
        if row is None:
            return None, why
        if int(months_ahead) < 1:
            return None, f"months_ahead must be 1 or more (1 = the front contract), not {months_ahead}"
        root = row.name
        if root in self._lme:
            return None, (f"{root} is an LME metal: no constant-maturity series, a prompt is read off the cash "
                          "and 3M closes")
        key = (root, int(months_ahead))
        frame = self._cm.get(key)
        if frame is None:
            wide = self._root_prices(root)
            if wide.empty:
                return None, wide.attrs.get("reason") or f"no closes on file for {root_id}"
            frame = self._build_cm_frame(root, int(months_ahead), wide, float(row["price_scale"]))
            self._cm[key] = frame
        if start is not None:
            frame = frame[frame.index >= pd.Timestamp(start)]
        return frame, ""

    def _build_cm_frame(self, root: str, months_ahead: int, wide: pd.DataFrame, scale: float) -> pd.DataFrame:
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
        """The close (quote units) of the `months_ahead`-th listed contract on each date, rolled on
        the contract's last trade date; `attrs['contracts']` the contract per date (`ContractMap`).
        Level only: use `constant_maturity_changes` for returns."""
        frame, why = self._cm_frame(root_id, months_ahead, start)
        name = f"{root_id} #{months_ahead}"
        if frame is None:
            return _empty(why, name)
        frame = frame.dropna(subset=["settle"])
        if frame.empty:
            return _empty(f"no close on file for contract #{months_ahead} of {root_id}", name)
        out = frame["settle"].rename(name)
        out.attrs.update(reason="", contracts=ContractMap(frame["contract_id"]))
        return out

    def constant_maturity_changes(self, root_id: str, months_ahead: int, start=None, raw: bool = False) -> pd.Series:
        """Day-on-day change of the constant-maturity contract, both days on the contract ranked on
        the later day. Quote units, or Bloomberg's quoted price with `raw=True`."""
        frame, why = self._cm_frame(root_id, months_ahead, start)
        name = f"{root_id} #{months_ahead}"
        if frame is None:
            return _empty(why, name)
        col = "raw_change" if raw else "change"
        frame = frame.dropna(subset=[col])
        if frame.empty:
            return _empty(f"no day-on-day change on file for contract #{months_ahead} of {root_id}", name)
        out = frame[col].rename(name)
        out.attrs.update(reason="", contracts=ContractMap(frame["contract_id"]))
        return out

    def months_ahead_of(self, contract_id: str, root_id: str, as_of=None) -> Tuple[Optional[int], str]:
        """The contract's rank on the listed strip on `as_of` (default: the last close): 1 = the
        front; an expired contract counts as the front. None for an LME prompt."""
        rid, why = self.resolve_contract(contract_id, root_id)
        if rid is None:
            return None, why
        if _LME_ID_RE.match(rid):
            return None, "an LME prompt has no rank on a strip: it is read off the cash and 3M closes"
        return self._rank_of(rid, as_of), ""

    def _rank_of(self, rid: str, as_of=None) -> int:
        root = self.contracts.at[rid, "instrument_id"]
        ids, ltds = self._strip(root)
        ref = pd.Timestamp(as_of or self.last_date).strftime("%Y-%m-%d")
        first_listed = bisect.bisect_right(ltds, ref)
        pos = ids.index(rid)
        return max(1, pos - first_listed + 1)

    # ------------------------------------------------------------ window moves
    def window_move_detail(self, root_id: str, months_to_expiry: float, start, end) -> dict:
        """The fractional change of ONE contract from the `start` close to the `end` close:
        {move, reason, contract_id, start_date, end_date, start_settle, end_settle} (raw). The
        start close is the root's last close on or before `start`; the contract the one listed then
        with a close that day whose last trade date is nearest `months_to_expiry` months away
        (30.4375 days a month, the nearer on a tie); no roll. An LME metal: the prompt that many
        months after the start close, read off the cash and 3M closes. Memoised per argument set."""
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
            target = float(months_to_expiry)
        except (TypeError, ValueError) as exc:
            out["reason"] = f"window {start} to {end} is not a pair of dates ({exc})"
            return out
        if t1 <= t0:
            out["reason"] = f"window {start} to {end}: the end is not after the start"
            return out
        root = row.name
        wide = self._root_prices(root)
        if wide.empty:
            out["reason"] = wide.attrs.get("reason") or f"no closes on file for {root_id}"
            return out
        before = wide.index[wide.index <= t0]
        if before.empty:
            out["reason"] = (f"no close of {root_id} on file on or before {t0:%Y-%m-%d} (the history starts "
                             f"{wide.index[0]:%Y-%m-%d})")
            return out
        d0 = before[-1]
        if root in self._lme:
            prompt = (d0 + pd.Timedelta(days=round(target * 30.4375))).date()
            cid = f"{root} {prompt.isoformat()}"
            own = self._lme_raw(root, prompt)
        else:
            ids, ltds = self._strip(root)
            first = bisect.bisect_right(ltds, d0.strftime("%Y-%m-%d"))
            at_d0 = wide.loc[d0]
            listed = [(c, t) for c, t in zip(ids[first:], ltds[first:])
                      if c in wide.columns and pd.notna(at_d0.get(c))]
            if not listed:
                out["reason"] = f"no {root_id} contract listed with a close on {d0:%Y-%m-%d}"
                return out
            cid, _ = min(listed, key=lambda c: (abs((pd.Timestamp(c[1]) - d0).days / 30.4375 - target), c[1]))
            own = wide[cid].dropna()
        out["contract_id"] = cid
        if d0 not in own.index:
            out["reason"] = f"{cid} ({target:g} months out on {d0:%Y-%m-%d}) has no close on that day"
            return out
        after = own[(own.index > d0) & (own.index <= t1)]
        if after.empty:
            out["reason"] = f"{cid} has no close after {d0:%Y-%m-%d} up to {t1:%Y-%m-%d}"
            return out
        s0, s1 = float(own.loc[d0]), float(after.iloc[-1])
        out.update(start_date=d0.strftime("%Y-%m-%d"), end_date=after.index[-1].strftime("%Y-%m-%d"),
                   start_settle=s0, end_settle=s1)
        if s0 <= 0:
            out["reason"] = f"{cid} closed at {s0:g} on {d0:%Y-%m-%d}: no fractional move from a price not above zero"
            return out
        out["move"] = s1 / s0 - 1.0
        return out

    def window_move(self, root_id: str, months_to_expiry: float, start, end) -> Tuple[Optional[float], str]:
        """(fractional change of one contract over the window, '') or (None, reason)."""
        d = self.window_move_detail(root_id, months_to_expiry, start, end)
        return d["move"], d["reason"]

    # ---------------------------------------------------------------------- fx
    def fx_series(self, pair: str) -> pd.Series:
        """The pair's daily close as stored (second currency per one of the first)."""
        if not self.available:
            return _empty(self.reason, pair)
        key = _norm(pair).replace(" ", "")
        if key not in self.fx.columns:
            return _empty(f"no price history on file for {pair}; on file: " + (", ".join(self.fx.columns) or "none"),
                          pair)
        out = self.fx[key].dropna().rename(key)
        out.attrs["reason"] = ""
        return out

    def usd_per_unit(self, currency: str, pair: Optional[str] = None) -> pd.Series:
        """USD per one unit of `currency`, daily, from `pair` or the default: USDCNH for CNY and
        CNH, else USD<ccy> inverted, else <ccy>USD as stored. `attrs['pair']` names it."""
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
        """USD P&L per day of `lots` contracts held (sign = direction): raw close change x
        `multiplier` (quote currency per 1.0 of Bloomberg's quoted price) x lots x USD per quote
        unit that day. Own closes where the contract has them; on and before its first close the
        constant-maturity change at its rank on `as_of`. An LME prompt: the change of its
        interpolated close. `fx`: None = the default pair, a pair name, or a Series of USD per
        quote unit. attrs: reason, research_contract_id (the id read), months_ahead, own_from,
        fallback_days, fx_pair, fx_missing_days (and note for an LME prompt). Memoised per
        argument set (a given `fx` Series is never kept); each call gets its own copy."""
        key = None
        if not isinstance(fx, pd.Series):
            try:
                key = (str(root_id), str(contract_id), float(lots), float(multiplier), str(currency),
                       None if fx is None else str(fx), None if as_of is None else str(as_of),
                       None if start is None else str(start))
            except (TypeError, ValueError):
                key = None
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
            return _empty(f"{root_id} is quoted in {row['currency']} in config/contracts.csv, not {currency}", name)
        rid, why = self.resolve_contract(contract_id, root_id)
        if rid is None:
            return _empty(why, name)
        own = self._raw_settles(rid)
        own_change = own.diff().iloc[1:]
        lme = bool(_LME_ID_RE.match(rid))
        n = None
        if lme:
            change = own_change
        else:
            n = self._rank_of(rid, as_of)
            # the constant-maturity raw changes straight off the kept frame, without its attrs
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
            if lme:
                return _empty(own.attrs.get("reason") or f"{contract_id}: no day-on-day change on file", name)
            return _empty(f"contract {contract_id} has no day-on-day change on file ({len(own)} close(s) of its own, "
                          f"none for contract #{n} of {row.name})", name)
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
            return _empty(f"contract {contract_id}: no day has both a close change and a {fx_pair} rate", name)
        own_from = own.index[0].strftime("%Y-%m-%d") if not own.empty else None
        fallback = 0 if lme else (int((usd.index <= own.index[0]).sum()) if not own.empty else int(len(usd)))
        usd.attrs.update(reason="", research_contract_id=rid, months_ahead=n, own_from=own_from,
                         fallback_days=fallback, fx_pair=fx_pair, fx_missing_days=missing)
        if lme:
            usd.attrs["note"] = self._lme_note(rid)
        return usd

    # --------------------------------------------------------------- liquidity
    def _liquidity_target(self, contract_id: str, root_id: Optional[str]) -> Tuple[Optional[str], Optional[str], dict]:
        """(the id read, our root id, extras) or (None, our root id, extras with 'reason').
        Extras: 'notes', 'underlying'."""
        extra: dict = {"notes": [], "underlying": None}
        text = " ".join(str(contract_id or "").split())
        lme = _LME_ID_RE.match(text.upper())
        if lme:
            root = root_id or lme.group("root")
            key = _norm(root).replace(" ", "")
            if key not in self._lme:
                extra["reason"] = f"LME prompt {lme.group('prompt')}: no cash and 3M price history on file for {root}"
                return None, root, extra
            extra["notes"].append("LME: the 3M pillar's figures; a ticket's own prompt has no volume or open "
                                  "interest of its own")
            return self._lme[key][1], root, extra
        opt = parse_option_ticker(text) if parse_bbg_ticker(text) is None else None
        if opt is not None:
            broot, code, digits, _, _, key = opt
            extra["underlying"] = f"{padded_root(broot)}{code}{digits} {key}"
            extra["reason"] = ("an option on a future: open interest and volume are kept for futures only; ask "
                               f"for its underlying {extra['underlying']}")
            return None, root_id or _our_root_of(extra["underlying"]), extra
        root = root_id or _our_root_of(text)
        rid, why = self.resolve_contract(text, root)
        if rid is None:
            extra["reason"] = why
            return None, root, extra
        return rid, root or self._root_of(rid), extra

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
            if day is None or pd.isna(day):
                rec["reason"] = f"as-of {as_of!r} is not a date"
                continue
            rid, our_root, extra = self._liquidity_target(cid, root)
            rec["root_id"] = our_root
            rec["underlying"] = extra["underlying"]
            if rid is None:
                rec["reason"] = extra.get("reason") or f"no price history on file for {cid}"
                continue
            plan[cid] = (rid, extra)
        if not plan:
            return out
        wanted = sorted({rid for rid, _ in plan.values()})
        try:
            df = self._read_ids(wanted, cols="open_interest, volume", until=as_of_iso)
        except Exception as exc:  # noqa: BLE001 -- a failed read is a reason, never a crash
            for cid in plan:
                out[cid]["reason"] = f"{self.path} could not be read ({type(exc).__name__}: {exc})"
            return out
        for col in ("open_interest", "volume"):      # -1 = not given
            if col in df.columns:
                df[col] = df[col].where(df[col] >= 0)
        groups = {k: g for k, g in df.groupby("contract_id", sort=False)}
        roots = load_roots()
        for cid, (rid, extra) in plan.items():
            rec = out[cid]
            notes: List[str] = list(extra["notes"])
            root_key = _norm(rec["root_id"] or "").replace(" ", "")
            rec.update(source_contract_id=rid, research_root=root_key or None)
            ours = roots.get(root_key)
            if ours is not None:
                rec.update(lot_size=float(ours.contract_size), lot_unit=ours.size_unit, exchange=ours.exchange)
            elif root_key:
                rec["exchange"] = root_key.split(":")[0]
            if rec["exchange"].upper() in CHINA_EXCHANGES:
                notes.append(_CHINA_NOTE.format(exchange=rec["exchange"].upper()))
            g = groups.get(rid)
            if g is None or g.empty:
                rec["reason"] = f"no open interest or volume on file for {rid} on or before {as_of_iso}"
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
                missing.append("no volume on file on or before " + as_of_iso)
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


# ------------------------------------------------------------------------ load
def _unavailable(path: str, reason: str, seen: Optional[List[dict]] = None) -> CommodityHistory:
    return CommodityHistory(False, path, reason, candidates=list(seen or []))


def _distinct_ids(conn: sqlite3.Connection) -> List[str]:
    """Every instrument_id of the table, by index skips (one seek per id, no full scan)."""
    rows = conn.execute(
        f"WITH RECURSIVE ids(x) AS (SELECT MIN(instrument_id) FROM {TABLE} UNION ALL "
        f"SELECT (SELECT MIN(instrument_id) FROM {TABLE} WHERE instrument_id > x) FROM ids WHERE x IS NOT NULL) "
        "SELECT x FROM ids WHERE x IS NOT NULL").fetchall()
    return [r[0] for r in rows]


def _add_months(year: int, month: int, n: int) -> Tuple[int, int]:
    k = year * 12 + month - 1 + n
    return k // 12, k % 12 + 1


def _build(conn: sqlite3.Connection, path: str) -> CommodityHistory:
    """The history object from an open read connection: the ids on file sorted into roots,
    contracts, LME pillars and FX pairs, the contract list with last trade dates (Bloomberg's
    stored ones, else contract-master's estimate) and the FX closes."""
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if TABLE not in names:
        return _unavailable(path, NO_HISTORY)
    ids = _distinct_ids(conn)
    if not ids:
        return _unavailable(path, NO_HISTORY)
    # Two queries, not one: MIN and MAX together scan the whole date index; each alone is a seek.
    first = conn.execute(f"SELECT MIN(as_of_date) FROM {TABLE}").fetchone()[0]
    last = conn.execute(f"SELECT MAX(as_of_date) FROM {TABLE}").fetchone()[0]
    static: Dict[str, str] = {}
    if "contract_static" in names:
        static = {str(c): str(d) for c, d in conn.execute("SELECT contract_id, last_trade_date FROM contract_static")}

    roots = load_roots()
    by_bbg = {(r.bbg_root.strip().upper(), r.bbg_yellow_key.upper()): r for r in roots.values()}
    held: Dict[str, Dict[Tuple[int, int], str]] = {}     # root -> {(year, month): id on file}
    lme: Dict[str, Dict[str, str]] = {}
    fx_ids: List[str] = []
    unknown: List[str] = []
    ids_by_root: Dict[str, List[str]] = {}
    for iid in ids:
        text = str(iid)
        pillar = _PILLAR_RE.match(text)
        if pillar:
            if pillar.group("root") in roots:
                lme.setdefault(pillar.group("root"), {})[pillar.group("pillar")] = text
                ids_by_root.setdefault(pillar.group("root"), []).append(text)
            else:
                unknown.append(text)
            continue
        parts = parse_bbg_ticker(text)
        if parts is not None and len(parts[2]) == 2:
            r = by_bbg.get((parts[0], parts[3].upper()))
            if r is not None:
                try:
                    held.setdefault(r.root_id, {})[(2000 + int(parts[2]), month_from_code(parts[1]))] = text
                    ids_by_root.setdefault(r.root_id, []).append(text)
                    continue
                except (KeyError, ValueError):
                    pass
            unknown.append(text)
            continue
        if _FX_RE.match(text):
            fx_ids.append(text)
            continue
        unknown.append(text)

    last_ym = (int(last[:4]), int(last[5:7])) if last else (2026, 1)
    rows = []
    for root_id, months in held.items():
        root = roots[root_id]
        cycle = set(root.active_months or range(1, 13))
        wanted = dict(months)
        y, m = min(months)
        end = max(max(months), _add_months(*last_ym, LIST_MONTHS_AHEAD))
        while (y, m) <= end:
            if m in cycle and (y, m) not in wanted:
                wanted[(y, m)] = make_contract_id(root.bbg_root, month_code(m), y, root.bbg_yellow_key)
            y, m = _add_months(y, m, 1)
        for (y, m), cid in wanted.items():
            ltd = static.get(cid)
            if not ltd:
                ltd = estimated_last_trade_date(m, y).isoformat()
            rows.append((cid, root_id, y, m, ltd[:10]))
    lme_pillars: Dict[str, Tuple[str, str]] = {}
    if lme:
        from engine.lme import monthly_prompt
        first_ym = (int(first[:4]), int(first[5:7])) if first else last_ym
        for root_id, pillars in lme.items():
            lme_pillars[root_id] = (pillars.get("CASH", f"{root_id} CASH"), pillars.get("3M", f"{root_id} 3M"))
            y, m = first_ym
            end = _add_months(*last_ym, LIST_MONTHS_AHEAD)
            while (y, m) <= end:
                prompt = monthly_prompt(y, m).isoformat()
                rows.append((f"{root_id} {prompt}", root_id, y, m, prompt))
                y, m = _add_months(y, m, 1)
    contracts = pd.DataFrame(rows, columns=["contract_id", "instrument_id", "year", "month", "last_trade_date"])
    contracts = contracts.sort_values(["instrument_id", "last_trade_date", "contract_id"]).set_index("contract_id")

    inst_rows = []
    for root_id in sorted(ids_by_root):
        r = roots[root_id]
        inst_rows.append({"instrument_id": root_id, "name": r.name, "sector": r.sector, "currency": r.currency,
                          "price_scale": float(r.price_scale), "bbg_root": r.bbg_root, "calendar_depth": np.nan,
                          "quote_unit": r.quote_unit, "contract_size": float(r.contract_size),
                          "size_unit": r.size_unit, "exchange": r.exchange})
    instruments = pd.DataFrame(inst_rows, columns=["instrument_id", "name", "sector", "currency", "price_scale",
                                                   "bbg_root", "calendar_depth", "quote_unit", "contract_size",
                                                   "size_unit", "exchange"]).set_index("instrument_id")

    if fx_ids:
        fx = pd.read_sql_query(f"SELECT instrument_id AS pair, as_of_date AS date, settle AS rate FROM {TABLE} "
                               "WHERE instrument_id IN (" + ",".join("?" * len(fx_ids)) + ")", conn,
                               params=tuple(fx_ids))
        fx["date"] = pd.to_datetime(fx["date"])
        fx_wide = fx.pivot(index="date", columns="pair", values="rate").sort_index().astype(float)
    else:
        fx_wide = pd.DataFrame(index=pd.DatetimeIndex([], name="date"))
    return CommodityHistory(True, path, "", last_date=last, first_date=first, instruments=instruments,
                            contracts=contracts, fx=fx_wide, unknown_ids=unknown, _ids=ids_by_root,
                            _lme=lme_pillars)


def _cache_key(path: Path) -> tuple:
    """(path, the database's (mtime_ns, size), config/contracts.csv's (mtime_ns, size)). The app
    runs SQLite in journal_mode=delete, so every write moves the database's stamp."""
    parts: list = [str(path.resolve())]
    for p in (path, Path(CONTRACTS_CSV)):
        try:
            st = p.stat()
            parts.append((st.st_mtime_ns, st.st_size))
        except OSError:
            parts.append(None)
    return tuple(parts)


def _conn_file(conn: sqlite3.Connection) -> Optional[str]:
    """The file of a connection's main database, or None for an in-memory one."""
    for _seq, name, file in conn.execute("PRAGMA database_list").fetchall():
        if name == "main":
            return file or None
    return None


def _load_memory(conn: sqlite3.Connection) -> CommodityHistory:
    """An in-memory book (tests, the golden book): its `price_history` and `contract_static`
    copied into a private in-memory database the history reads from. Not cached."""
    own = sqlite3.connect(":memory:", check_same_thread=False)
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for table in (TABLE, "contract_static"):
            if table not in names:
                continue
            ddl = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()[0]
            own.execute(ddl)
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            rows = conn.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
            own.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", rows)
        if TABLE in names:
            own.execute(f"CREATE INDEX IF NOT EXISTS ix_{TABLE}_date ON {TABLE} (as_of_date)")
        own.commit()
        hist = _build(own, ":memory:")
    except Exception as exc:  # noqa: BLE001 -- a book that will not read is a reason, never a crash
        own.close()
        return _unavailable(":memory:", f"the book's price history could not be read ({type(exc).__name__}: {exc})")
    hist._own = own
    hist.candidates = [{"path": ":memory:", "exists": True}]
    if hist.available:
        hist.note = f"price history in the in-memory book (closes {hist.first_date} to {hist.last_date})"
    return hist


_CACHE: Dict[tuple, CommodityHistory] = {}
_CACHE_SLOTS = 4
_LOAD_LOCK = threading.Lock()   # one load per database identity, even with warm() running


def load_commodity_history(path: Union[str, Path, sqlite3.Connection, None] = None) -> CommodityHistory:
    """The price history of a book database: `path` its file, or an open sqlite3 connection to
    it (an in-memory one is copied and not cached); None = the app's default database
    (`data.paths.get_db_path()`: RISK_DB, else data/raw/risk.db). Cached per database stamp.
    Never raises: a missing file, table or row gives `available = False` with its reason."""
    if isinstance(path, sqlite3.Connection):
        try:
            file = _conn_file(path)
        except Exception as exc:  # noqa: BLE001 -- a closed connection is a reason, never a crash
            return _unavailable("", f"the book's connection could not be read ({type(exc).__name__}: {exc})")
        if file is None:
            return _load_memory(path)
        path = file
    if path is None:
        from data.paths import get_db_path
        path = get_db_path()
    db = Path(path).expanduser()
    seen = [{"path": str(db), "exists": db.is_file()}]
    if not db.is_file():
        return _unavailable(str(db), f"{NO_HISTORY} (no book database at {db})", seen)
    key = _cache_key(db)
    hit = _CACHE.get(key)
    if hit is None:
        with _LOAD_LOCK:
            hit = _CACHE.get(key)
            if hit is None:
                try:
                    conn = _connect(db)
                    try:
                        hit = _build(conn, str(db))
                    finally:
                        conn.close()
                except Exception as exc:  # noqa: BLE001 -- a file that will not read is a reason
                    hit = _unavailable(str(db), f"{db} could not be read ({type(exc).__name__}: {exc})")
                if "could not be read" not in hit.reason:
                    if len(_CACHE) >= _CACHE_SLOTS:
                        _CACHE.clear()
                    _CACHE[key] = hit
    hit.candidates = seen
    hit.note = f"price history in {db} (closes {hit.first_date} to {hit.last_date})" if hit.last_date else ""
    return hit


def window_move(root_id: str, months_to_expiry: float, start, end,
                path: Union[str, Path, sqlite3.Connection, None] = None) -> Tuple[Optional[float], str]:
    """`CommodityHistory.window_move` on `load_commodity_history(path)`."""
    return load_commodity_history(path).window_move(root_id, months_to_expiry, start, end)


def contract_liquidity(contract_ids, as_of, window: int = LIQUIDITY_WINDOW,
                       db_path: Union[str, Path, sqlite3.Connection, None] = None) -> Dict[str, dict]:
    """Each contract's open interest and average daily volume from `price_history` (Bloomberg's
    OPEN_INT and PX_VOLUME; -1 on file = not given), for a liquidity check of position size.
    A risk input only: never a mark, never in P&L or delta. Never raises.

    `contract_ids`: our canonical ids, or a dict {our id: our root_id}. Also accepted: an LME
    ticket's 'LME:CA 2026-12-10' (read from the metal's 3M pillar, said in `note`) and an option id
    (no figures: its `underlying` future id is given to ask for).

    Returns {our id: {contract_id, root_id, source_contract_id (the id read), research_root (our
    root, kept name), open_interest (lots, the latest on or before `as_of`), oi_date, adv (mean
    daily volume over the last `window` days with a volume), adv_days, window, volume_last,
    volume_date, lot_size, lot_unit, exchange, underlying, label, as_of, reason ('' when both
    figures are there), note (caveats: Chinese open-interest counting, LME), source_kind
    ('bloomberg'), source_note ('')}}. A value not on file is None, with the reason."""
    return load_commodity_history(db_path).contract_liquidity(contract_ids, as_of, window)


# ------------------------------------------------------------------- warm-up
def _book_roots(book_db: Union[str, Path]) -> List[str]:
    """The contract roots ('NYMEX:CL', 'LME:CA') of every trade on file in the book database."""
    conn = _connect(Path(book_db))
    try:
        rows = conn.execute("SELECT DISTINCT i.base_ccy FROM trades t JOIN instruments i "
                            "ON i.instrument_id = t.instrument_id WHERE i.base_ccy LIKE '%:%'").fetchall()
    finally:
        conn.close()
    return sorted(r[0] for r in rows if r[0])


def warm(root_ids=None, db_path: Union[str, Path, None] = None, book_db: Union[str, Path, None] = None) -> dict:
    """Preload what the first Risk, Book or P&L screen would read cold: the history of the book
    database (`db_path`, else `book_db`, else the default), its contract lookups, and the closes
    of `root_ids` (or of every root with a trade on file) in one pass. For a background thread;
    read-only; never raises. Returns {ok, seconds: {load, lookup, roots}, roots, reason}."""
    out = {"ok": False, "seconds": {}, "roots": [], "reason": ""}
    try:
        db = db_path if db_path is not None else book_db
        t = time.perf_counter()
        hist = load_commodity_history(db)
        out["seconds"]["load"] = round(time.perf_counter() - t, 3)
        if not hist.available:
            out["reason"] = hist.reason
            return out
        t = time.perf_counter()
        hist.resolve_contract("")        # builds the id lookup
        hist._by_month()
        out["seconds"]["lookup"] = round(time.perf_counter() - t, 3)
        if root_ids is not None:
            roots = list(root_ids)
        else:
            roots = _book_roots(db if db is not None else hist.path)
        out["roots"] = sorted({str(r) for r in roots if r})
        t = time.perf_counter()
        if out["roots"]:
            hist.prefetch_roots(out["roots"])
        out["seconds"]["roots"] = round(time.perf_counter() - t, 3)
        out["ok"] = True
    except Exception as exc:  # noqa: BLE001 -- a warm-up that fails leaves the cold path as it was
        out["reason"] = f"warm-up stopped ({type(exc).__name__}: {exc})"
    return out
