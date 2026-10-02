"""The commodity underlyers of the Risk tab (commodity conversion, Phase 4): the book's
commodity positions held constant across Bloomberg's daily price history (the book database's
`price_history`, read by `engine.risk.commodity_history`, risk-history's), the same way `metrics.py` holds the FX
delta constant across Bloomberg's FX closes (`engine.risk.history`).

Positions are the book's own, never recomputed here:
  * `engine.curve.curve_positions(conn, as_of)["rows"]` (curve-positions): one row per open
    commodity future, option on one and LME forward, with its `delta_lots` (futures-equivalent
    lots: an averaging contract's shrinking share, an option's DELTA mark times its lots).
  * `engine.spreads.book_spreads(conn, as_of)["spreads"]` (spreads-engine): each open spread's
    legs with their `open_lots`.
No mark is read here and nothing is written anywhere.

The daily USD P&L of one contract held (`CommodityHistory.daily_pnl_series_for_position`):
  close change (Bloomberg's quoted price) x our multiplier x lots x USD per quote unit that day
  (a CNY contract through USDCNH); its own closes while it has them, the constant-maturity
  changes at the same depth before. The history of
    * a FUTURE is its own contract's;
    * a CMDTY_OPTION is its underlying future's (`underlying_id`), at the option's delta lots;
    * an LME_FWD is its own prompt's ('LME:CA 2026-12-10'), which the history reads off the
      metal's cash and 3M closes, linear in time between them (a prompt that is not a date
      takes its month's monthly prompt, the third Wednesday).
Each series stops at `as_of`. The per-lot series of a contract is computed once and scaled
(the P&L is linear in lots).

Underlyers (kinds) and their role:
  * COMMODITY, one per contract root ('NYMEX:CL'): the sum of its contracts' series at their
    delta lots. A PART: the book's series is the sum of the parts.
  * SECTOR, one per sector: the sum of its COMMODITY rows' series. A VIEW (it re-adds parts).
  * SPREAD, one per open spread of spreads-engine: the sum of its futures legs' series at their
    open lots. A VIEW: its legs are already inside the COMMODITY rows.
A contract with no history (or no delta) is left out of its row's sum and named in the row's
reason ("excludes 1 of 2 contracts ..."); a row with nothing left is NaN with the reasons.

Net / gross USD of a row: COMMODITY and SECTOR take curve-positions' `net_delta_usd` /
`gross_delta_usd` (None -> NaN with its reason); a SPREAD's are its leftover outright
(spreads-engine's `leftover` USD notional, summed and summed in absolute value), because a
clean spread has no net outright by construction.
"""
from __future__ import annotations

import datetime as dt
import math
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

KIND_COMMODITY = "COMMODITY"
KIND_SECTOR = "SECTOR"
KIND_SPREAD = "SPREAD"
ROLE_PART = "part"
ROLE_VIEW = "view"

NAN = float("nan")
_OPTION = "CMDTY_OPTION"
_LME = "LME_FWD"
_FUTURE = "FUTURE"


def _num(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return NAN
    return v if math.isfinite(v) else NAN


def _isnan(v: float) -> bool:
    return v != v


def _norm_ccy(ccy: Any) -> str:
    return str(ccy or "").strip().upper()


def _join(*parts: str) -> str:
    return "; ".join(p for p in parts if p)


def _sum_series(series: List[pd.Series]) -> Optional[pd.Series]:
    if not series:
        return None
    out = pd.concat(series, axis=1).sum(axis=1, min_count=1).dropna().sort_index()
    out.index.name = "date"
    return out if len(out) else None


def fx_future_pair(root_id: str) -> Optional[str]:
    """The currency pair an FX future moves with ('USDCNH' for the SGX USD/CNH future), from
    contract-master's root: its size unit (the currency a lot is sized in, USD) then its quote
    currency (CNH); None for a root that is not in the ``fx`` sector (spreads-engine's
    `engine.spreads.hedges.is_hedge`, the book's one hedge test, called, never copied) or whose
    units are not two currencies. Never a ticker or id match: the root decides."""
    try:
        from data.contracts import load_roots
        from engine.spreads.hedges import is_hedge
        root = load_roots().get(str(root_id or "").replace(" ", "").upper())
    except Exception:  # noqa: BLE001 -- no universe: not an FX future
        return None
    if root is None or not is_hedge(root, _FUTURE):
        return None
    base, quote = str(root.size_unit or "").strip().upper(), str(root.currency or "").strip().upper()
    if len(base) != 3 or len(quote) != 3 or not base.isalpha() or not quote.isalpha() or base == quote:
        return None
    return f"{base}{quote}"


class _PerLot:
    """Per-lot USD P&L series per (root, contract, multiplier, currency), cut at as_of, memoised
    for one `book_risk` call.

    An FX future (a root of contract-master's ``fx`` sector: the SGX USD/CNH future) moves on its
    own contract's closes when the price history has them; otherwise (none pulled yet, or a
    contract id stored under an older Bloomberg root, 'UCV26 Curncy' for root XUC) on its pair's
    daily close in the same history ('USDCNH', CNH per USD, `fx_future_pair`): the close change x
    our multiplier (100,000 USD a lot) x lots x USD per quote unit that day, the formula of a
    contract's own closes (`CommodityHistory.daily_pnl_series_for_position`), so its P&L is in CNH
    converted at the day's USDCNH, as the valuation converts it. A risk input only: nothing is
    written, no mark is read."""

    def __init__(self, history, as_of: str):
        self.history = history
        self.as_of = as_of
        self.cut = pd.Timestamp(as_of)
        self._memo: Dict[tuple, Tuple[Optional[pd.Series], dict, str]] = {}

    def get(self, root_id: str, contract_id: str, multiplier: float, currency: str) -> Tuple[Optional[pd.Series], dict, str]:
        key = (root_id, contract_id, float(multiplier), currency)
        if key not in self._memo:
            s = self.history.daily_pnl_series_for_position(root_id, contract_id, 1.0, multiplier, currency,
                                                           as_of=self.as_of)
            attrs = {k: v for k, v in s.attrs.items()}
            why = attrs.get("reason", "")
            if s.empty:
                self._memo[key] = (None, attrs, why or f"contract {contract_id}: no history")
            else:
                s = s[s.index <= self.cut]
                if s.empty:
                    self._memo[key] = (None, attrs, f"contract {contract_id}: no settlement change on or before "
                                                    f"{self.as_of} in the Bloomberg price history")
                else:
                    self._memo[key] = (s.astype(float), attrs, "")
            if self._memo[key][0] is None:
                pair = fx_future_pair(root_id)
                if pair is not None:
                    self._memo[key] = self._pair_per_lot(pair, contract_id, multiplier, currency, self._memo[key][2])
        return self._memo[key]

    def _pair_per_lot(self, pair: str, contract_id: str, multiplier: float, currency: str, own_why: str
                      ) -> Tuple[Optional[pd.Series], dict, str]:
        """(per-lot USD P&L of an FX future on its pair's close, attrs, why None): see the class."""
        from engine.risk.commodity_history import FX_TOLERANCE_DAYS
        h = self.history
        if not getattr(h, "available", False):
            return None, {}, own_why or getattr(h, "reason", "") or "no price history"
        close = h.fx_series(pair)
        if close.empty:
            return None, {}, (f"{own_why}; and no {pair} closes to move it on "
                              f"({close.attrs.get('reason') or 'none on file'})")
        close = close.astype(float).sort_index()
        close.index = pd.to_datetime(close.index)
        change = close.diff().iloc[1:]
        change = change[change.index <= self.cut]
        local = change * float(multiplier)
        fx_pair, missing = "", 0
        if _norm_ccy(currency) != "USD":
            conv = h.usd_per_unit(currency)
            fx_pair = conv.attrs.get("pair", "")
            if conv.empty:
                return None, {}, f"{own_why}; and no USD conversion for {currency} ({conv.attrs.get('reason', '')})"
            conv = conv.astype(float).sort_index()
            conv.index = pd.to_datetime(conv.index)
            merged = pd.merge_asof(pd.DataFrame({"date": local.index, "pnl": local.to_numpy()}),
                                   pd.DataFrame({"date": conv.index, "usd": conv.to_numpy()}),
                                   on="date", direction="backward", tolerance=pd.Timedelta(days=FX_TOLERANCE_DAYS))
            missing = int(merged["usd"].isna().sum())
            usd = pd.Series((merged["pnl"] * merged["usd"]).to_numpy(), index=local.index)
        else:
            usd = local
        usd = usd.dropna().astype(float)
        usd.index.name = "date"
        if usd.empty:
            return None, {}, f"{own_why}; and no {pair} close change on or before {self.as_of}"
        attrs = {"reason": "", "research_contract_id": pair, "months_ahead": None, "own_from": None,
                 "fallback_days": int(len(usd)), "fx_pair": fx_pair, "fx_missing_days": missing,
                 "note": (f"{contract_id}: an FX future with no closes of its own in the price history, so it moves "
                          f"with the {pair} close ({pair[3:]} per {pair[:3]}) x {float(multiplier):,.0f} a lot, "
                          f"converted at the day's {fx_pair or pair}")}
        return usd.rename(contract_id), attrs, ""


def lme_history_contract(history, root_id: str, year: Optional[int], month: Optional[int],
                         prompt: str, as_of: str) -> Tuple[Optional[str], str, str]:
    """(the id an LME forward's history is read from, a note, a reason): the ticket's own prompt,
    f"{root} {prompt}" ('LME:CA 2026-12-10'), which risk-history's reader interpolates in time
    between the metal's cash and 3M closes. A `prompt` that is not a date takes the monthly
    prompt (the third Wednesday) of `year`-`month`. `as_of` is kept for the callers' signature."""
    if not getattr(history, "available", False):
        return None, "", getattr(history, "reason", "") or "no price history"
    root = str(root_id or "").replace(" ", "").upper()
    day = None
    try:
        day = dt.date.fromisoformat(str(prompt or "")[:10])
    except ValueError:
        if year is not None and month is not None:
            try:
                from engine.lme import monthly_prompt
                day = monthly_prompt(int(year), int(month))
            except Exception as exc:  # noqa: BLE001 -- a month that will not resolve is a reason
                return None, "", f"LME prompt month {year}-{month}: no monthly prompt ({type(exc).__name__}: {exc})"
    if day is None:
        return None, "", f"LME prompt {prompt!r} is not a date"
    cid, why = history.resolve_contract(f"{root} {day.isoformat()}", root)
    if cid is None:
        return None, "", why or f"no cash and 3M price history on file for {root}"
    return cid, f"LME prompt {day.isoformat()}: interpolated in time between the {root} cash and 3M closes", ""


def _contract_detail(row: dict) -> dict:
    return {"contract_id": row.get("contract_id"), "product": row.get("product"), "history_contract": None,
            "delta_lots": row.get("delta_lots"), "multiplier": row.get("multiplier"),
            "currency": row.get("currency", ""), "months_ahead": None,
            "own_from": None, "fallback_days": None, "fx_pair": "", "fx_missing_days": None,
            "days": 0, "in_series": False, "reason": "", "note": ""}


def contract_series(row: dict, per_lot: _PerLot, as_of: str) -> Tuple[dict, Optional[pd.Series]]:
    """(the detail of one curve-positions row, its daily USD P&L at its delta lots or None)."""
    d = _contract_detail(row)
    product = row.get("product")
    root_id = row.get("root_id") or ""
    lots = _num(row.get("delta_lots"))
    mult = _num(row.get("multiplier"))
    if _isnan(lots):
        d["reason"] = row.get("reason") or f"{d['contract_id']}: no delta on {as_of}"
        return d, None
    if _isnan(mult):
        d["reason"] = row.get("reason") or f"{d['contract_id']}: no multiplier"
        return d, None
    if product == _OPTION:
        hist_id = row.get("underlying_id")
        if not hist_id:
            d["reason"] = row.get("reason") or f"option {d['contract_id']}: its underlying future is not known"
            return d, None
        d["note"] = f"an option: the history of its underlying {hist_id}, at its delta in futures-equivalent lots"
    elif product == _LME:
        hist_id, note, why = lme_history_contract(per_lot.history, root_id, row.get("year"), row.get("month"),
                                                  str(row.get("expiry") or ""), as_of)
        if hist_id is None:
            d["reason"] = why
            return d, None
        d["note"] = note
    else:
        hist_id = row.get("contract_id")
    d["history_contract"] = hist_id
    unit, attrs, why = per_lot.get(root_id, hist_id, mult, d["currency"])
    if attrs.get("research_contract_id"):          # the id the history read (risk-history's attr name)
        d["history_contract"] = str(attrs["research_contract_id"])
    if attrs.get("note") and not d["note"]:        # an FX future on its pair's closes (`_PerLot`)
        d["note"] = str(attrs["note"])
    for k in ("months_ahead", "own_from", "fallback_days", "fx_pair", "fx_missing_days"):
        if k in attrs:
            v = attrs[k]
            d[k] = int(v) if k in ("months_ahead", "fallback_days", "fx_missing_days") and v is not None else v
    if unit is None:
        d["reason"] = why
        return d, None
    s = unit * lots
    d["days"] = int(len(s))
    d["in_series"] = True
    return d, s


def _excludes(details: List[dict], what: str = "contracts") -> str:
    out = [x for x in details if not x["in_series"]]
    if not out:
        return ""
    return (f"excludes {len(out)} of {len(details)} {what} with no history: "
            + "; ".join(f"{x['contract_id']}: {x['reason']}" for x in out))


def commodity_underlyers(conn: sqlite3.Connection, as_of: str, history, curve: dict,
                         spreads: Optional[dict], per_lot: Optional[_PerLot] = None
                         ) -> Tuple[List[dict], Dict[str, pd.Series], List[str]]:
    """(rows, {row key: daily USD P&L}, missing) for the COMMODITY, SECTOR and SPREAD
    underlyers (module docstring). Each row carries `key` (unique across the Risk tab's rows),
    `role`, `parts` (the row keys a view sums) and `contracts` / `legs` (the per-contract detail).
    `per_lot`: a `_PerLot` to share with the position risk (`positions.py`), else a new one."""
    per_lot = per_lot if per_lot is not None else _PerLot(history, as_of)
    rows: List[dict] = []
    series: Dict[str, pd.Series] = {}
    missing: List[str] = []
    by_commodity = curve.get("by_commodity") or {}
    by_sector = curve.get("by_sector") or {}
    curve_rows = curve.get("rows") or []

    # COMMODITY: one per root, in curve-positions' order
    root_multiplier: Dict[str, float] = {}
    per_root: Dict[str, List[Tuple[dict, Optional[pd.Series]]]] = {}
    for r in curve_rows:
        per_root.setdefault(r.get("root_id") or "", []).append(contract_series(r, per_lot, as_of))
        if r.get("product") in (_FUTURE, _OPTION) and not _isnan(_num(r.get("multiplier"))):
            root_multiplier.setdefault(r.get("root_id") or "", _num(r.get("multiplier")))
    for root_id, pairs in per_root.items():
        c = by_commodity.get(root_id) or {}
        details = [d for d, _ in pairs]
        s = _sum_series([x for _, x in pairs if x is not None])
        key = f"{KIND_COMMODITY}:{root_id}"
        net, gross = _num(c.get("net_delta_usd")), _num(c.get("gross_delta_usd"))
        delta_why = "" if not (_isnan(net) or _isnan(gross)) else (c.get("delta_reason") or "no USD delta")
        reason = (_join(delta_why, _excludes(details)) if s is not None
                  else _join(delta_why, "no history: " + "; ".join(f"{d['contract_id']}: {d['reason']}" for d in details)))
        sector = c.get("sector") or next((r.get("sector") for r in curve_rows if r.get("root_id") == root_id), "") or ""
        rows.append({"key": key, "underlyer": root_id, "name": c.get("name", root_id), "kind": KIND_COMMODITY,
                     "role": ROLE_PART, "series": root_id, "sector": sector, "net_usd": net,
                     "gross_usd": gross, "net_delta_lots": c.get("net_delta_lots"), "carry": False,
                     "reason": reason, "note": "Bloomberg daily price history (risk input only)",
                     "parts": [], "contracts": details})
        if s is not None:
            series[key] = s
        for d in details:
            if not d["in_series"]:
                missing.append(f"{d['contract_id']}: not in {root_id}'s series ({d['reason']})")

    # SECTOR: one per sector of curve-positions, the sum of its commodity rows
    for sector, sec in by_sector.items():
        roots = [x for x in (sec.get("commodities") or []) if x in per_root]
        keys = [f"{KIND_COMMODITY}:{x}" for x in roots]
        parts = [k for k in keys if k in series]
        s = _sum_series([series[k] for k in parts])
        net, gross = _num(sec.get("net_delta_usd")), _num(sec.get("gross_delta_usd"))
        delta_why = "" if not (_isnan(net) or _isnan(gross)) else (sec.get("delta_reason") or "no USD delta")
        absent = [x for x in roots if f"{KIND_COMMODITY}:{x}" not in series]
        excl = (f"excludes {len(absent)} of {len(roots)} commodities with no history: {', '.join(absent)}"
                if absent and s is not None else "")
        none = ""
        if s is None:
            none = (f"no history: {history.reason}" if not getattr(history, "available", False)
                    else f"no commodity of {sector} has a history series")
        key = f"{KIND_SECTOR}:{sector}"
        rows.append({"key": key, "underlyer": sector, "name": sector, "kind": KIND_SECTOR, "role": ROLE_VIEW,
                     "series": sector, "sector": sector, "net_usd": net, "gross_usd": gross, "carry": False,
                     "reason": _join(delta_why, none, excl),
                     "note": f"view: the sum of its commodities' series ({', '.join(roots) or 'none'})",
                     "parts": parts})
        if s is not None:
            series[key] = s

    # SPREAD: one per open spread, its futures legs at their open lots
    for sp in (spreads or {}).get("spreads") or []:
        if sp.get("status") != "open":
            continue
        legs, parts_s = [], []
        for leg in sp.get("legs") or []:
            lots = _num(leg.get("open_lots"))
            detail = {"contract_id": leg.get("instrument_id"), "root_id": leg.get("root_id"),
                      "product": leg.get("product"), "open_lots": leg.get("open_lots"),
                      "currency": leg.get("currency", ""), "in_series": False, "reason": "", "days": 0}
            if _isnan(lots) or lots == 0.0:
                continue                                          # a closed leg holds nothing
            if leg.get("product") != _FUTURE:
                detail["reason"] = f"a {leg.get('product')} leg: only futures legs are in a spread's series"
                legs.append(detail)
                continue
            mult = root_multiplier.get(leg.get("root_id") or "", NAN)
            if _isnan(mult):
                mult = _instrument_multiplier(conn, leg.get("instrument_id"))
            if _isnan(mult):
                detail["reason"] = f"{leg.get('instrument_id')}: no multiplier on file"
                legs.append(detail)
                continue
            unit, _attrs, why = per_lot.get(leg.get("root_id") or "", leg.get("instrument_id"), mult, detail["currency"])
            if unit is None:
                detail["reason"] = why
            else:
                detail.update(in_series=True, days=int(len(unit)))
                parts_s.append(unit * lots)
            legs.append(detail)
        if not legs:
            continue
        s = _sum_series(parts_s)
        left = sp.get("leftover") or []
        vals = [_num(x.get("usd_notional")) for x in left]
        if any(_isnan(v) for v in vals):
            net = gross = NAN
            delta_why = "leftover outright has no USD figure: " + "; ".join(
                f"{x.get('root_id')}: {x.get('reason') or 'no USD notional'}" for x, v in zip(left, vals) if _isnan(v))
        else:
            net, gross, delta_why = float(sum(vals)), float(sum(abs(v) for v in vals)), ""
        name = sp.get("name") or sp.get("spread_id") or "spread"
        key = f"{KIND_SPREAD}:{sp.get('spread_id') or name}"
        reason = (_join(delta_why, _excludes(legs, "legs")) if s is not None
                  else _join(delta_why, "no history: " + "; ".join(f"{d['contract_id']}: {d['reason']}" for d in legs)))
        rows.append({"key": key, "underlyer": name, "name": name, "kind": KIND_SPREAD, "role": ROLE_VIEW,
                     "series": name, "sector": "", "spread_id": sp.get("spread_id"), "spread_kind": sp.get("kind"),
                     "family": sp.get("family"), "net_usd": net, "gross_usd": gross, "carry": False,
                     "reason": reason,
                     "note": "view: its legs' series at their open lots; net / gross USD = its leftover outright",
                     "parts": [d["contract_id"] for d in legs if d["in_series"]], "legs": legs})
        if s is not None:
            series[key] = s
    return rows, series, missing


def _instrument_multiplier(conn: sqlite3.Connection, instrument_id: Optional[str]) -> float:
    if not instrument_id:
        return NAN
    try:
        hit = conn.execute("SELECT multiplier FROM instruments WHERE instrument_id = ?", (instrument_id,)).fetchone()
    except sqlite3.Error:
        return NAN
    return _num(hit[0]) if hit else NAN
