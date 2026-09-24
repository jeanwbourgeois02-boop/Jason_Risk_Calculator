"""``book_spreads(conn, as_of)``: the book's spreads, outrights and review list, with P&L.

Everything P&L here is ``value_book`` rows added up: a spread's LTD is the sum of its trades'
``pnl_usd``; a period is ``LTD(as_of) - LTD(reference close)`` over the same trades, the
reference close chosen by pnl-series' own ``engine.pnl.reference.resolve_reference`` (the step
back of up to 5 business days and the per-trade fill the header uses) on the dates of
``engine.pnl.ledger.period_reference_dates``. Nothing is re-marked, converted or filled here:
a leg in CNY is in the spread at the USD figure ``value_book`` gave it. A spread with a leg
unpriced has no figure, with that leg's reason, never a partial sum.

The one number computed here that ``value_book`` does not give is the leftover's USD notional:
leftover lots x ``instruments.multiplier`` x that contract's mark x its spot, both read off the
leg's own ``value_book`` row (the mark and conversion its P&L used); no mark is looked up.
"""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from data.contracts import UnknownContract, contract_for, load_roots
from engine.pnl.calendar import load_holidays
from engine.pnl.ledger import period_reference_dates
from engine.pnl.reference import resolve_reference
from engine.pnl.valuation import value_book
from engine.spreads import grouping
from engine.spreads.grouping import CALENDAR, Leg, Match, Shape
from engine.spreads.overrides import PIN, SPLIT, ensure_overrides_table, override_problems, read_overrides
from engine.spreads.templates import Template, load_templates

PERIODS = ("ltd", "daily", "d5", "mtd", "ytd")
SPREAD_PRODUCTS = ("FUTURE",)       # what the automatic rule groups (options on futures: Phase 5)

KIND_BUNDLE = "bundle"
KIND_PINNED = "pinned"
REVIEW_AMBIGUOUS = "ambiguous"      # two ways to group the same trades
REVIEW_RATIO = "ratio_off"          # a calendar / template's legs, signs that fit, lots outside 5 %
REVIEW_ACCOUNTS = "accounts"        # a single-currency calendar / template on the same day, on two accounts

ValueFn = Callable[[sqlite3.Connection, str], object]


# ------------------------------------------------------------------ valuation frames
class _Frames:
    """The whole book valued once per date (``value_fn``, ``value_book`` by default; a screen may
    pass its own filled reader, whose (frame, ...) tuple is accepted), scoped per group."""

    def __init__(self, conn: sqlite3.Connection, value_fn: ValueFn):
        self.conn, self.value_fn, self.cache = conn, value_fn, {}

    def __call__(self, day: str) -> pd.DataFrame:
        if day not in self.cache:
            out = self.value_fn(self.conn, day)
            self.cache[day] = out[0] if isinstance(out, tuple) else out
        return self.cache[day]

    def scoped(self, day: str, ids: frozenset) -> pd.DataFrame:
        df = self(day)
        return df[df["trade_id"].isin(ids)] if not df.empty else df


def _num(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _priced(row) -> bool:
    return not str(row.get("reason", "") or "") and _num(row.get("pnl_usd")) is not None


def _why(row) -> str:
    return str(row.get("reason", "") or "") or "no USD P&L on its row"


def _period_pnl(ids: Sequence[str], as_of: str, frames: _Frames, refs: Dict[str, str], holidays) -> dict:
    """{pnl_usd: {period: value | None}, pnl_reasons: {period: ''|why}, pnl_notes, ref_dates}."""
    ids = frozenset(ids)
    values = {p: None for p in PERIODS}
    reasons = {p: "" for p in PERIODS}
    notes = {p: "" for p in PERIODS}
    ref_used = {p: (as_of if p == "ltd" else refs[p]) for p in PERIODS}
    df_a = frames.scoped(as_of, ids)
    rows = {r["trade_id"]: r for r in df_a.to_dict("records")} if not df_a.empty else {}
    missing = sorted(ids - set(rows))
    unpriced = [f"{tid} ({_why(r)})" for tid, r in sorted(rows.items()) if not _priced(r)]
    if missing or unpriced:
        parts = []
        if unpriced:
            parts.append(f"unpriced on {as_of}: " + "; ".join(unpriced))
        if missing:
            parts.append(f"not valued by value_book on {as_of}: {', '.join(missing)}")
        why = " and ".join(parts)
        return {"pnl_usd": values, "pnl_reasons": {p: why for p in PERIODS}, "pnl_notes": notes, "ref_dates": ref_used}
    ltd = float(sum(float(r["pnl_usd"]) for r in rows.values()))
    values["ltd"] = ltd
    for p in PERIODS[1:]:
        ref = refs[p]
        try:
            choice = resolve_reference(df_a, ref, lambda d: frames.scoped(d, ids), holidays)
        except Exception as exc:  # noqa: BLE001 -- one close that cannot be valued blanks this figure only
            reasons[p] = f"the {ref} close could not be valued ({type(exc).__name__}: {exc})"
            continue
        frame = choice.frame
        ref_used[p], notes[p] = choice.ref_date_used, choice.note
        if not choice.found or choice.split.blocked_ids:
            blocked = choice.split.blocked_ids
            fr = {r["trade_id"]: r for r in frame.to_dict("records")} if not frame.empty else {}
            detail = "; ".join(f"{t} ({_why(fr.get(t, {}))})" for t in sorted(blocked))
            reasons[p] = (f"unpriced on the {choice.ref_date_used} close: {detail}" if detail
                          else f"no usable close on {ref}")
            if not choice.found:
                reasons[p] = f"{reasons[p]}. {choice.exhausted_sentence}".strip()
            continue
        ref_sum = float(sum(float(r["pnl_usd"]) for r in frame.to_dict("records") if _priced(r)))
        values[p] = ltd - ref_sum
    return {"pnl_usd": values, "pnl_reasons": reasons, "pnl_notes": notes, "ref_dates": ref_used}


# ------------------------------------------------------------------ trades
def _has_column(conn, table: str, column: str) -> bool:
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))


def _read_trades(conn: sqlite3.Connection, as_of: str) -> List[dict]:
    theme = "COALESCE(NULLIF(t.theme, ''), it.theme, '')" if _has_column(conn, "trades", "theme") \
        else "COALESCE(it.theme, '')"
    sql = f"""
        SELECT t.trade_id, t.instrument_id, t.product, t.trade_date, t.quantity, t.account,
               {theme} AS theme, i.base_ccy, i.quote_ccy, i.multiplier, i.expiry_date
        FROM trades_official t JOIN instruments i USING (instrument_id)
        LEFT JOIN instrument_theme it ON it.instrument_id = t.instrument_id
        WHERE t.trade_date <= :as_of ORDER BY t.trade_id"""
    cols = ("trade_id", "instrument_id", "product", "trade_date", "quantity", "account", "theme",
            "base_ccy", "quote_ccy", "multiplier", "expiry_date")
    return [dict(zip(cols, r)) for r in conn.execute(sql, {"as_of": as_of})]


def _month_key(trade: dict, roots) -> str:
    """'2026-12' from contract-master for a known root; '' when it cannot say (the leg then
    orders by the instrument's expiry date)."""
    if trade["base_ccy"] in roots:
        try:
            cm = contract_for(trade["base_ccy"], trade["instrument_id"])
            return f"{cm.year:04d}-{cm.month:02d}"
        except (UnknownContract, ValueError):
            pass
    return ""


# ------------------------------------------------------------------ the rule
class _Book:
    def __init__(self, conn, as_of, value_fn, templates_dir):
        self.conn, self.as_of = conn, as_of
        self.reasons: List[str] = []
        templates, problems = load_templates(templates_dir)
        self.reasons += list(problems)
        self.by_root: Dict[str, List[Template]] = defaultdict(list)
        for t in templates:
            for r in dict.fromkeys(t.roots):
                self.by_root[r].append(t)
        self.roots = load_roots()
        self.meta = {rid: (r.quote_unit, r.exchange_code) for rid, r in self.roots.items()}
        ensure_overrides_table(conn)      # a read-only database without it simply has no overrides
        self.overrides = read_overrides(conn)
        self.reasons += override_problems(conn)
        self.trades = _read_trades(conn, as_of)
        self.by_id = {t["trade_id"]: t for t in self.trades}
        for t in self.trades:
            t["month_key"] = _month_key(t, self.roots)
        self.frames = _Frames(conn, value_fn)
        self.today = {r["trade_id"]: r for r in self.frames(as_of).to_dict("records")} \
            if not self.frames(as_of).empty else {}
        self.refs = period_reference_dates(as_of)
        self.holidays = load_holidays()
        self.problem_set: Dict[str, None] = {}

    # ---- helpers
    def lots(self, tid: str) -> Optional[float]:
        return _num(self.by_id[tid]["quantity"])

    def is_open(self, tid: str) -> bool:
        return str(self.today.get(tid, {}).get("status", "")) == "OPEN"

    def root_of(self, tid: str) -> str:
        return str(self.by_id[tid]["base_ccy"] or "")

    def legs_of(self, tids: Iterable[str], per_group: bool) -> List[Leg]:
        """Net lots per contract (per account and trade date too when ``per_group``); a contract
        whose trades net to zero there gives no leg."""
        acc: Dict[tuple, List[str]] = defaultdict(list)
        for tid in tids:
            t = self.by_id[tid]
            key = (t["account"], t["trade_date"], t["instrument_id"]) if per_group else ("", "", t["instrument_id"])
            acc[key].append(tid)
        legs = []
        for (account, day, inst), ids in sorted(acc.items()):
            lots = sum(self.lots(i) or 0.0 for i in ids)
            if abs(lots) < 1e-9:
                continue
            t = self.by_id[ids[0]]
            legs.append(Leg(inst, str(t["base_ccy"]), account, day, lots, tuple(sorted(ids)),
                            t["month_key"] or str(t["expiry_date"])))
        return legs

    # ---- the rule
    def group(self) -> dict:
        bundles: Dict[str, List[str]] = defaultdict(list)
        pinned: Dict[str, List[str]] = defaultdict(list)
        candidates: List[str] = []
        outright_why: Dict[str, str] = {}
        for t in self.trades:
            tid = t["trade_id"]
            ov = self.overrides.get(tid)
            if t["theme"]:
                bundles[t["theme"]].append(tid)
            elif ov and ov["action"] == PIN:
                pinned[ov["group_name"]].append(tid)
            elif t["product"] not in SPREAD_PRODUCTS:
                continue                                      # FX hedges, FX options: not the rule's
            elif ov and ov["action"] == SPLIT:
                outright_why[tid] = "split by hand (spread_overrides)"
            elif t["base_ccy"] not in self.roots:
                outright_why[tid] = f"root {t['base_ccy']!r} is not in config/contracts.csv, so it is not grouped"
            elif self.lots(tid) is None:
                outright_why[tid] = f"trades.quantity {t['quantity']!r} is not a number, so it is not grouped"
            else:
                candidates.append(tid)

        spreads: List[dict] = []
        for name, tids in sorted(bundles.items()):
            spreads.append(self.hand_made(f"BUNDLE-{name}", name, KIND_BUNDLE, tids))
        for name, tids in sorted(pinned.items()):
            spreads.append(self.hand_made(f"PIN-{name}", name, KIND_PINNED, tids))

        # same account, same trade date
        legs = self.legs_of(candidates, per_group=True)
        in_legs = {tid for leg in legs for tid in leg.trade_ids}
        for tid in candidates:
            if tid not in in_legs:
                outright_why[tid] = "bought and sold back the same day on the same account: a round trip"
        # one trade date at a time: a match may take its legs from one account, or, for a template
        # whose legs span currencies (China against the West), from several accounts of the book
        by_day: Dict[str, List[Leg]] = defaultdict(list)
        for leg in legs:
            by_day[leg.trade_date].append(leg)
        used, reviewed = set(), set()
        review: List[dict] = []
        near: List[Match] = []
        cross: List[Match] = []
        for _day, day_legs in sorted(by_day.items()):
            matches = self.candidates(day_legs)
            allowed = [m for m in matches if len(m.accounts) == 1 or self.multi_currency(m)]
            full = grouping.dedupe(m for m in allowed if m.matched)
            clean, conflicts = grouping.split_conflicts(full)
            for m in clean:
                spreads.append(self.auto(m))
                used |= m.leg_keys
            for comp in conflicts:
                reviewed |= frozenset().union(*(m.leg_keys for m in comp))
                review.append(self.review_entry(REVIEW_AMBIGUOUS, comp))
            near += [m for m in allowed if not m.matched]
            cross += [m for m in matches if len(m.accounts) > 1 and not self.multi_currency(m)]
        taken = used | reviewed
        for m in grouping.widest(grouping.dedupe((m for m in near if not m.leg_keys & taken), closest_first=True)):
            review.append(self.review_entry(REVIEW_RATIO, [m]))
        # a single-currency calendar or template on two accounts: never grouped, listed for review
        for m in grouping.widest(grouping.dedupe((m for m in cross if not m.leg_keys & taken), closest_first=True)):
            review.append(self.review_entry(REVIEW_ACCOUNTS, [m]))

        grouped = {tid for s in spreads for tid in s["trade_ids"]}
        review_of: Dict[str, List[str]] = defaultdict(list)
        for r in review:
            for tid in r["trade_ids"]:
                review_of[tid].append(r["review_id"])
        outrights = [self.outright(tid, outright_why.get(tid, ""), review_of.get(tid, []))
                     for tid in sorted(set(candidates) | set(outright_why)) if tid not in grouped]
        return {"spreads": spreads, "outrights": outrights, "review": review}

    def multi_currency(self, m: Match) -> bool:
        """True when the match's legs are quoted in more than one currency (a CNY leg against a USD
        leg): the one case whose legs may sit on different accounts."""
        return len({self.roots[leg.root_id].currency for leg in m.legs if leg.root_id in self.roots}) > 1

    def candidates(self, legs: Sequence[Leg]) -> List[Match]:
        matches, problems = grouping.candidates(legs, self.by_root, self.meta)
        for p in problems:
            if p not in self.problem_set:
                self.problem_set[p] = None
                self.reasons.append(p)
        return matches

    # ---- output rows
    def leg_rows(self, tids: Sequence[str], order: Sequence[str] = ()) -> List[dict]:
        by_inst: Dict[str, List[str]] = defaultdict(list)
        for tid in tids:
            by_inst[self.by_id[tid]["instrument_id"]].append(tid)
        rank = {inst: n for n, inst in enumerate(order)}
        out = []
        for inst, ids in sorted(by_inst.items(), key=lambda kv: (rank.get(kv[0], len(rank)),
                                                                  self.by_id[kv[1][0]]["base_ccy"], kv[0])):
            t = self.by_id[ids[0]]
            rows = [self.today.get(i) for i in ids]
            local = [_num(r.get("pnl_local")) if r else None for r in rows]
            usd_ok = all(r is not None and _priced(r) for r in rows)
            why = "; ".join(f"{i} ({_why(r) if r else 'not valued by value_book'})"
                            for i, r in zip(ids, rows) if r is None or not _priced(r))
            out.append({
                "trade_ids": sorted(ids), "instrument_id": inst, "root_id": str(t["base_ccy"] or ""),
                "product": t["product"], "contract_month": t["month_key"],
                "lots": sum(self.lots(i) or 0.0 for i in ids),
                "open_lots": sum((self.lots(i) or 0.0) for i in ids if self.is_open(i)),
                "currency": str(t["quote_ccy"] or ""),
                "pnl_local": sum(local) if all(v is not None for v in local) else None,
                "pnl_usd": sum(float(r["pnl_usd"]) for r in rows) if usd_ok else None,
                "status": "open" if any(self.is_open(i) for i in ids) else "closed",
                "reason": why,
            })
        return out

    def leftover(self, legs: Sequence[Leg], shape: Optional[Shape]) -> Tuple[List[dict], str]:
        """(per root: lots and USD notional left outright, basis) on the OPEN lots of each leg."""
        open_lots = [sum((self.lots(i) or 0.0) for i in leg.trade_ids if self.is_open(i)) for leg in legs]
        if shape is not None:
            left = grouping.leftover_lots(open_lots, shape)
            basis = f"beyond the {shape.name} ratio" if shape.kind != CALENDAR else "net of the calendar"
        else:
            left = tuple(open_lots)
            basis = "net per root (no calendar or template fits these legs)"
        per_root: Dict[str, dict] = {}
        for leg, lots in zip(legs, left):
            entry = per_root.setdefault(leg.root_id, {"root_id": leg.root_id, "lots": 0.0, "usd_notional": 0.0,
                                                      "reason": ""})
            entry["lots"] = round(entry["lots"] + lots, 9)
            if lots == 0.0:
                continue
            notional, why = self.notional(leg, lots)
            if notional is None:
                entry["usd_notional"] = None
                entry["reason"] = "; ".join(x for x in (entry["reason"], why) if x)
            elif entry["usd_notional"] is not None:
                entry["usd_notional"] += notional
        for entry in per_root.values():
            if abs(entry["lots"]) < 1e-9:
                entry["lots"] = 0.0
        return sorted(per_root.values(), key=lambda e: e["root_id"]), basis

    def notional(self, leg: Leg, lots: float) -> Tuple[Optional[float], str]:
        t = self.by_id[leg.trade_ids[0]]
        mult = _num(t["multiplier"])
        for tid in leg.trade_ids:
            r = self.today.get(tid) or {}
            mark, spot = _num(r.get("mark")), _num(r.get("spot"))
            if mark is not None and spot is not None and mult is not None and r.get("status") == "OPEN":
                return lots * mult * mark * spot, ""
        r = self.today.get(leg.trade_ids[0]) or {}
        return None, (f"{leg.instrument_id}: no price or USD conversion on {self.as_of} for the leftover's "
                      f"notional ({_why(r) if r else 'not valued by value_book'})")

    def base(self, spread_id: str, name: str, kind: str, tids: Sequence[str]) -> dict:
        tids = sorted(tids)
        out = {
            "spread_id": spread_id, "name": name, "kind": kind, "template": "", "family": "", "unit": "",
            "size": None, "size_unit": "", "deviation": None, "also_matches": [],
            "trade_ids": tids,
            "accounts": sorted({str(self.by_id[t]["account"]) for t in tids}),
            "trade_dates": sorted({str(self.by_id[t]["trade_date"]) for t in tids}),
            "status": "open" if any(self.is_open(t) for t in tids) else "closed",
        }
        out.update(_period_pnl(tids, self.as_of, self.frames, self.refs, self.holidays))
        return out

    def auto(self, m: Match) -> dict:
        out = self.base(f"SPREAD-{m.trade_ids[0]}", m.shape.name,
                        CALENDAR if m.shape.kind == CALENDAR else m.shape.kind, m.trade_ids)
        self._shape_fields(out, m)
        out["legs"] = self.leg_rows(m.trade_ids, [leg.instrument_id for leg in m.legs])
        for row, w in zip(out["legs"], m.shape.weights):
            row["weight"] = w
        out["leftover"], out["leftover_basis"] = self.leftover(m.legs, m.shape)
        return out

    def hand_made(self, spread_id: str, name: str, kind: str, tids: Sequence[str]) -> dict:
        out = self.base(spread_id, name, kind, tids)
        futs = [t for t in tids if self.by_id[t]["product"] in SPREAD_PRODUCTS and self.root_of(t) in self.roots]
        legs = self.legs_of(futs, per_group=False)
        m = grouping.best_cover(legs, self.by_root, self.meta) if len(legs) >= 2 else None
        if m is not None:
            self._shape_fields(out, m)
            legs = list(m.legs)
        out["legs"] = self.leg_rows(tids, [leg.instrument_id for leg in legs])
        if m is not None:
            weights = dict(zip((leg.instrument_id for leg in m.legs), m.shape.weights))
            for row in out["legs"]:
                row["weight"] = weights.get(row["instrument_id"])
        out["leftover"], out["leftover_basis"] = self.leftover(legs, m.shape if m else None)
        return out

    @staticmethod
    def _shape_fields(out: dict, m: Match) -> None:
        out.update(template="" if m.shape.kind == CALENDAR else m.shape.kind, family=m.shape.family,
                   unit=m.shape.unit, size=m.size, size_unit=m.shape.quantity_unit, deviation=m.deviation,
                   also_matches=[s.kind for s in m.also])

    def review_entry(self, kind: str, matches: Sequence[Match]) -> dict:
        tids = sorted({t for m in matches for leg in m.legs for t in leg.trade_ids})
        cands = [{"kind": m.shape.kind, "name": m.shape.name, "trade_ids": list(m.trade_ids),
                  "size": m.size, "size_unit": m.shape.quantity_unit, "deviation": m.deviation,
                  "also_matches": [s.kind for s in m.also]} for m in matches]
        if kind == REVIEW_AMBIGUOUS:
            reason = ("these trades can be grouped more than one way ("
                      + "; ".join(f"{c['name']}: {', '.join(c['trade_ids'])}" for c in cands)
                      + "), so none is taken; bundle them to choose")
        else:
            m = matches[0]
            lots = ", ".join(f"{leg.instrument_id} {leg.lots:+g}" for leg in m.legs)
            ideal = ", ".join(f"{leg.instrument_id} {m.implied[0] * w / u:+.4g}"
                              for leg, w, u in zip(m.legs, m.shape.weights, m.shape.units_per_lot))
            ratio = (f"lots {lots}; the ratio would need {ideal} (off by {m.deviation:.1%}, "
                     f"tolerance {grouping.TOLERANCE:.0%})")
            if kind == REVIEW_RATIO:
                reason = f"looks like {m.shape.name} but the {ratio}; left as outrights, bundle them to group"
            else:
                fits = "the lots fit the ratio" if m.matched else ratio
                reason = (f"looks like {m.shape.name} but the legs are on {len(m.accounts)} accounts "
                          f"({', '.join(m.accounts)}) and the rule groups a single-currency spread on one "
                          f"account only; {fits}; left as outrights, bundle them to group")
        legs = [leg for m in matches for leg in m.legs]
        return {
            "review_id": f"{kind}|{'+'.join(tids)}", "kind": kind, "trade_ids": tids,
            "instruments": sorted({leg.instrument_id for leg in legs}),
            "accounts": sorted({leg.account for leg in legs}),
            "trade_dates": sorted({leg.trade_date for leg in legs}),
            "candidates": cands, "reason": reason,
        }

    def outright(self, tid: str, why: str, review_ids: List[str]) -> dict:
        t = self.by_id[tid]
        leg = self.leg_rows([tid])[0]
        out = {
            "trade_id": tid, "instrument_id": t["instrument_id"], "root_id": leg["root_id"],
            "contract_month": leg["contract_month"], "account": t["account"], "trade_date": t["trade_date"],
            "lots": leg["lots"], "currency": leg["currency"], "status": leg["status"],
            "pnl_local": leg["pnl_local"], "why_outright": why, "review_ids": list(review_ids),
        }
        out.update(_period_pnl([tid], self.as_of, self.frames, self.refs, self.holidays))
        return out


def book_spreads(conn: sqlite3.Connection, as_of: str, value_fn: ValueFn = value_book,
                 templates_dir=None) -> dict:
    """The book's futures grouped into spreads by the rule of ``engine/spreads/__init__.py``.

    Returns ``{as_of, spreads, outrights, review, reasons}``:

    - ``spreads``: one dict per spread, bundles first, then hand pins, then the rule's, by first
      trade id: ``spread_id`` ('BUNDLE-<name>', 'PIN-<name>', 'SPREAD-<min trade id>'), ``name``,
      ``kind`` ('bundle' | 'pinned' | 'calendar' | the template id), ``template``, ``family``,
      ``unit`` (the spread's price unit), ``size`` / ``size_unit`` (signed, + = long the spread as
      the template writes it; a calendar in lots, + = long the nearer month), ``deviation`` (how
      far the lots are from the ratio), ``also_matches`` (other templates grouping the same
      trades), ``trade_ids``, ``accounts``, ``trade_dates``, ``status`` ('open' while any leg is
      open, else 'closed'), ``legs`` ([{trade_ids, instrument_id, root_id, product,
      contract_month ('2026-12'), lots, open_lots, currency, pnl_local, pnl_usd, status, reason,
      weight}]; ``pnl_usd`` the leg's LTD), ``pnl_usd`` ({ltd, daily, d5, mtd, ytd}: a figure or
      None), ``pnl_reasons`` (same keys: '' or why it is None), ``pnl_notes`` (the reference's
      step-back or fill caption), ``ref_dates`` (the close each period is measured from),
      ``leftover`` ([{root_id, lots, usd_notional, reason}] on the open lots: 0 for a clean
      spread; ``usd_notional`` None with its reason when the leg's price or spot is missing) and
      ``leftover_basis``.
    - ``outrights``: every futures trade the rule left alone: trade_id, instrument_id, root_id,
      contract_month, account, trade_date, lots, currency, status, pnl_local, ``why_outright``
      ('' = no spread fits), ``review_ids`` (the review entries naming it), and the same
      ``pnl_usd`` / ``pnl_reasons`` / ``pnl_notes`` / ``ref_dates`` as a spread.
    - ``review``: groups the rule refused to take, never guessed ('ambiguous': two ways to group;
      'ratio_off': a calendar or template's legs whose lots are outside 5 %; 'accounts': a
      single-currency calendar or template on two accounts): review_id, kind, trade_ids,
      instruments, accounts, trade_dates, candidates, reason. Their trades stay in ``outrights``.
    - ``reasons``: book-level sentences (a template that could not be read or sized, an override
      that could not be applied).

    ``value_fn(conn, day)``: the valuation read, ``value_book`` by default (unfilled, as the
    engine's own figures are); a screen may pass its filled reader for its own view.
    """
    book = _Book(conn, as_of, value_fn, templates_dir)
    out = book.group()
    return {"as_of": as_of, **out, "reasons": book.reasons}
