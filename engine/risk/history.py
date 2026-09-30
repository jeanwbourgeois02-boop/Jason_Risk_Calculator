"""The FX and metal price history of the Risk tab's metrics, read from the book database.

The currency and metal underlyers of `engine.risk.metrics` (EUR, JPY, CNH, XAU, ...) are
valued on Bloomberg's daily closes of their USD pairs, the rows bbg-backfill writes into the
book database's own `price_history` table when the user presses "Pull Bloomberg now"
(`instrument_id` = the pair as stored: 'USDCNH', 'EURUSD', 'USDJPY', 'GBPUSD', 'XAUUSD'; `settle`
= Bloomberg's daily PX_LAST; listed by `data.bloomberg.library.risk_history_needs` as kind 'FX'
for every non-USD currency and metal of the open book). The app reads no other app's files
(user decision 2026-09-30).

`load_history(db)` loads them through `engine.risk.commodity_history.load_commodity_history`,
the same reader and cache as the commodity rows, and serves:

  * `History.spot`: one column per currency, USD per ONE unit of it on a DatetimeIndex named
    `date` (EUR 1.17, JPY 0.0067, CNH 0.14, XAU USD per ounce). A 'USD<ccy>' pair is inverted,
    a '<ccy>USD' pair taken as stored, 'USD<ccy>' first when both are on file; CNY and CNH both
    through USDCNH (`commodity_history.DEFAULT_FX_PAIR`, the rule `risk_history_needs` asks by).
    A cross with no USD side is not a column. `History.pairs` names the pair of each column.
  * `History.yields`: always empty. No short-rate history is pulled for risk, so the metrics
    run on the spot move alone and say so (the carry term of `engine.risk.metrics`).

`db` is the book database's path or an open sqlite3 connection to it (an in-memory one is
copied, like the commodity history); None = `data.paths.get_db_path()`. Cached per database
stamp with the commodity history it derives from. With no FX rows, `available` is False with
`NO_HISTORY` as its reason: never an exception to the caller.

This history is a RISK input only. Nothing read here is ever written to `marks`, used as a
mark, or used for P&L or delta (hard rule 2), and nothing here asks Bloomberg (hard rule 8):
the positions the metrics apply it to come from `engine.ladder.positions.book_positions`, the
app's own official marks.
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import pandas as pd

from engine.risk.commodity_history import DEFAULT_FX_PAIR, NO_HISTORY, CommodityHistory, load_commodity_history

__all__ = ["History", "NO_HISTORY", "SPOT_LABEL", "YIELDS_FILE", "YIELDS_REASON", "load_history"]

# What `files` calls its two entries (the names `engine.risk.metrics` quotes in its notes).
SPOT_LABEL = "FX price history"
YIELDS_FILE = "short-rate history"
YIELDS_REASON = "no short-rate history is pulled for risk: the daily move is the spot move alone"
_METALS = ("XAU", "XAG", "XPT", "XPD")


@dataclass
class History:
    """The loaded history. `files` has two entries, `spot` and `yields`: {file, path, loaded,
    rows, columns, first_date, last_date, reason}. `last_date` is the spot frame's last date
    (ISO). `status()` is the JSON-friendly summary `book_risk` publishes."""
    available: bool
    path: str
    reason: str = ""
    spot: pd.DataFrame = field(default_factory=pd.DataFrame)
    yields: pd.DataFrame = field(default_factory=pd.DataFrame)
    last_date: Optional[str] = None
    files: Dict[str, dict] = field(default_factory=dict)
    note: str = ""                                          # the database and how far its FX closes run
    candidates: List[dict] = field(default_factory=list)   # [{path, exists}]: the database looked at
    pairs: Dict[str, str] = field(default_factory=dict)    # column (currency) -> the pair it is read from

    def status(self) -> dict:
        return {"available": self.available, "path": self.path, "last_date": self.last_date,
                "reason": self.reason, "note": self.note, "candidates": [dict(c) for c in self.candidates],
                "files": {k: dict(v) for k, v in self.files.items()}, "pairs": dict(self.pairs)}


def _file_status(label: str, path: str, df: Optional[pd.DataFrame], reason: str) -> dict:
    out = {"file": label, "path": path, "loaded": df is not None and not df.empty, "rows": 0, "columns": [],
           "first_date": None, "last_date": None, "reason": reason}
    if df is not None and len(df):
        out["rows"] = int(len(df))
        out["columns"] = [str(c) for c in df.columns]
        out["first_date"] = df.index[0].strftime("%Y-%m-%d")
        out["last_date"] = df.index[-1].strftime("%Y-%m-%d")
    return out


def _currencies(pairs) -> List[str]:
    """Every currency a stored pair gives in USD: 'USDJPY' -> JPY, 'EURUSD' -> EUR; CNY with CNH."""
    out = set()
    for p in pairs:
        p = str(p).upper()
        if len(p) != 6 or p == "USDUSD":
            continue
        if p.startswith("USD"):
            out.add(p[3:])
        elif p.endswith("USD"):
            out.add(p[:3])
    if "CNH" in out:
        out.add("CNY")
    return sorted(out, key=lambda c: (c in _METALS, c))


def _derive(ch: CommodityHistory) -> History:
    """The FX history from the book's price history: USD per unit per currency."""
    seen = [dict(c) for c in ch.candidates] or [{"path": ch.path, "exists": bool(ch.path)}]
    yields_status = _file_status(YIELDS_FILE, ch.path, None, YIELDS_REASON)
    if not ch.available:
        reason = ch.reason or NO_HISTORY
        return History(False, ch.path, reason, candidates=seen,
                       files={"spot": _file_status(SPOT_LABEL, ch.path, None, reason), "yields": yields_status})
    columns: Dict[str, pd.Series] = {}
    pairs: Dict[str, str] = {}
    skipped: List[str] = []
    for ccy in _currencies(ch.fx.columns):
        s = ch.usd_per_unit(ccy)
        if s.empty:
            skipped.append(f"{ccy}: {s.attrs.get('reason') or 'no closes'}")
            continue
        s = s[s > 0].dropna()
        if s.empty:
            skipped.append(f"{ccy}: no close above zero")
            continue
        columns[ccy] = s.astype(float)
        pairs[ccy] = str(s.attrs.get("pair") or DEFAULT_FX_PAIR.get(ccy, ""))
    if not columns:
        reason = f"{NO_HISTORY}: no FX pair with a USD side in the book's price history"
        return History(False, ch.path, reason, candidates=seen,
                       files={"spot": _file_status(SPOT_LABEL, ch.path, None, reason), "yields": yields_status})
    spot = pd.DataFrame(columns).sort_index()
    spot.index = pd.DatetimeIndex(spot.index, name="date")
    spot = spot[~spot.index.duplicated(keep="last")]
    last = spot.index[-1].strftime("%Y-%m-%d")
    note = (f"FX closes in {ch.path} ({spot.index[0]:%Y-%m-%d} to {last}): "
            + ", ".join(f"{c} from {p}" for c, p in pairs.items()))
    if skipped:
        note += "; not used: " + "; ".join(skipped)
    return History(True, ch.path, "", spot=spot, last_date=last, note=note, candidates=seen, pairs=pairs,
                   files={"spot": _file_status(SPOT_LABEL, ch.path, spot, ""), "yields": yields_status})


# One derived History per commodity history object (itself cached per database stamp); the
# object is held beside its History, so its id is never reused while the entry lives.
_MEMO: Dict[int, Tuple[CommodityHistory, History]] = {}
_MEMO_SLOTS = 4
_MEMO_LOCK = threading.Lock()


def load_history(db: Union[str, Path, sqlite3.Connection, None] = None) -> History:
    """The FX and metal history of the book database `db` (a path or an open sqlite3
    connection; None = `data.paths.get_db_path()`). Never raises: no database, table or FX row
    gives `available = False` with its reason."""
    try:
        ch = load_commodity_history(db)
    except Exception as exc:  # noqa: BLE001 -- a book that will not read is a reason, never a crash
        path = str(db) if isinstance(db, (str, Path)) else ""
        reason = f"the book's price history could not be read ({type(exc).__name__}: {exc})"
        return History(False, path, reason, files={"spot": _file_status(SPOT_LABEL, path, None, reason),
                                                   "yields": _file_status(YIELDS_FILE, path, None, YIELDS_REASON)})
    with _MEMO_LOCK:
        hit = _MEMO.get(id(ch))
        if hit is not None and hit[0] is ch:
            return hit[1]
    out = _derive(ch)
    if ch.path and ch.path != ":memory:":
        with _MEMO_LOCK:
            if len(_MEMO) >= _MEMO_SLOTS:
                _MEMO.clear()
            _MEMO[id(ch)] = (ch, out)
    return out
