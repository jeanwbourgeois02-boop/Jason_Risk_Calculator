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

The result is cached per (path, the database's and its WAL file's mtime and size): in
WAL mode the main file's mtime does not move on a write until a checkpoint, so the WAL
file is part of the key. Inside that cached object, and so per database identity, are
kept: the settlements read per root, the constant-maturity frame per (root, months ahead)
and each position's P&L per argument set (2026-09-25: the Risk tab and the header's VaR
chip rebuilt them on every call, about 11 s of a 14 s `book_risk`). A changed database
is a new object, so nothing kept outlives the data it was built from.
"""
from __future__ import annotations

import bisect
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

from data.contracts.tickers import month_from_code, parse_bbg_ticker

__all__ = ["CommodityHistory", "DEFAULT_FX_PAIR", "DEFAULT_PATH", "ENV_VAR", "LABEL", "candidates",
           "load_commodity_history", "research_curve", "window_move"]

LABEL = "research"

ENV_VAR = "COMMODITY_HISTORY_DB"
_REPO = Path(__file__).resolve().parents[2]
DEFAULT_PATH = _REPO.parent / "Commodity Dashboard" / "var" / "rv.sqlite"
TABLES = ("instrument", "contract", "price_daily", "fx_daily")
CONNECT_TIMEOUT_SECONDS = 5.0
FX_TOLERANCE_DAYS = 7
# The pair each currency converts through when the caller names none (the research
# app's fx_daily rule: "convert CNY prices with USDCNH unless a spread names USDCNY").
DEFAULT_FX_PAIR = {"CNY": "USDCNH", "CNH": "USDCNH"}


def _empty(reason: str, name: Optional[str] = None) -> pd.Series:
    s = pd.Series([], index=pd.DatetimeIndex([], name="date"), dtype=float, name=name)
    s.attrs["reason"] = reason
    return s


def _copy(s: pd.Series) -> pd.Series:
    """A copy of a kept series with its own attrs (a caller may set attrs on what it gets)."""
    out = s.copy()
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
    _roots: Dict[str, pd.DataFrame] = field(default_factory=dict, repr=False)
    _lookup: Dict[str, dict] = field(default_factory=dict, repr=False)
    _cm: Dict[tuple, pd.DataFrame] = field(default_factory=dict, repr=False)      # (root, months_ahead) -> frame
    _pnl: Dict[tuple, pd.Series] = field(default_factory=dict, repr=False)        # position P&L per argument set

    # ----------------------------------------------------------------- summary
    def status(self) -> dict:
        return {"available": self.available, "path": self.path, "reason": self.reason,
                "first_date": self.first_date, "last_date": self.last_date, "note": self.note,
                "candidates": [dict(c) for c in self.candidates],
                "roots": int(len(self.instruments)), "contracts": int(len(self.contracts)),
                "fx_pairs": [str(c) for c in self.fx.columns]}

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
        hit = self.contracts[(self.contracts["instrument_id"] == root) & (self.contracts["year"] == year)
                             & (self.contracts["month"] == month)]
        if hit.empty:
            return None, f"contract {contract_id} ({root_id} {code}{year}) is not in the research database ({self.path})"
        return hit.index[0], ""

    def _one_digit_year(self, digit: int) -> int:
        ref = int((self.last_date or "2026")[:4])
        year = (ref // 10) * 10 + digit
        return year + 10 if year < ref - 1 else year

    # ------------------------------------------------------------------ prices
    def _root_prices(self, root_id: str) -> pd.DataFrame:
        """date x contract_id of RAW settles for one root (research ids), memoised. A read that
        fails gives an empty frame with `attrs['reason']`, not memoised."""
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
            if df.empty:
                wide = pd.DataFrame(index=pd.DatetimeIndex([], name="date"))
            else:
                df["date"] = pd.to_datetime(df["date"])
                wide = df.pivot_table(index="date", columns="contract_id", values="settle", aggfunc="last").sort_index()
                wide.index.name = "date"
            self._roots[root_id] = wide.astype(float)
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
        per date. Level only: its day-on-day difference jumps at a roll; use
        `constant_maturity_changes` for returns."""
        frame, why = self._cm_frame(root_id, months_ahead, start)
        name = f"{root_id} #{months_ahead}"
        if frame is None:
            return _empty(why, name)
        frame = frame.dropna(subset=["settle"])
        if frame.empty:
            return _empty(f"root {root_id} has no settlement for contract #{months_ahead} in the research database", name)
        out = frame["settle"].rename(name)
        out.attrs.update(reason="", contracts=frame["contract_id"].to_dict())
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
        out.attrs.update(reason="", contracts=frame["contract_id"].to_dict())
        return out

    def months_ahead_of(self, contract_id: str, root_id: str, as_of=None) -> Tuple[Optional[int], str]:
        """The contract's rank on the listed strip on `as_of` (default: the database's last
        date): 1 = the front. A contract already expired on that date counts as the front."""
        research_id, why = self.resolve_contract(contract_id, root_id)
        if research_id is None:
            return None, why
        root = self.contracts.at[research_id, "instrument_id"]
        ids, ltds = self._strip(root)
        ref = pd.Timestamp(as_of or self.last_date).strftime("%Y-%m-%d")
        first_listed = bisect.bisect_right(ltds, ref)
        pos = ids.index(research_id)
        return max(1, pos - first_listed + 1), ""

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
               "source": "", "rows": []}
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
        n, why = self.months_ahead_of(contract_id, root_id, as_of)
        if n is None:
            return _empty(why, name)
        own = self._raw_settles(research_id)
        own_change = own.diff().iloc[1:]
        cm = self.constant_maturity_changes(row.name, n, raw=True)
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
                return CommodityHistory(False, str(path), f"{path} is not the research app's database "
                                        f"(no {', '.join(missing)} table)")
            inst_cols = {r[1] for r in conn.execute("PRAGMA table_info(instrument)")}
            unit_col = "quote_unit" if "quote_unit" in inst_cols else "'' AS quote_unit"
            instruments = pd.read_sql_query(
                "SELECT instrument_id, name, sector, currency, price_scale, bbg_root, calendar_depth, "
                f"{unit_col} FROM instrument", conn)
            contracts = pd.read_sql_query(
                "SELECT contract_id, instrument_id, year, month, last_trade_date FROM contract", conn)
            fx = pd.read_sql_query("SELECT pair, date, rate FROM fx_daily", conn)
            first, last = conn.execute("SELECT MIN(date), MAX(date) FROM price_daily").fetchone()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 -- a file that will not read is a reason, never a crash
        return CommodityHistory(False, str(path), f"{path} could not be read ({type(exc).__name__}: {exc})")
    instruments = instruments.set_index("instrument_id")
    contracts = contracts.set_index("contract_id")
    if fx.empty:
        fx_wide = pd.DataFrame(index=pd.DatetimeIndex([], name="date"))
    else:
        fx["date"] = pd.to_datetime(fx["date"])
        fx_wide = fx.pivot_table(index="date", columns="pair", values="rate", aggfunc="last").sort_index().astype(float)
    if last is None:
        return CommodityHistory(False, str(path), f"{path} holds no settlements (price_daily is empty)",
                                instruments=instruments, contracts=contracts, fx=fx_wide)
    return CommodityHistory(True, str(path), "", last_date=last, first_date=first, instruments=instruments,
                            contracts=contracts, fx=fx_wide)


_CACHE: Dict[tuple, CommodityHistory] = {}
_CACHE_SLOTS = 4


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
        return CommodityHistory(False, seen[0]["path"] if seen else "",
                                "no commodity history database: tried " + ", ".join(c["path"] for c in seen),
                                candidates=seen)
    db = Path(found[0]["path"])
    key = _cache_key(db)
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
    stale_days, unit, currency, price_scale, reason, note, source, rows}:
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
