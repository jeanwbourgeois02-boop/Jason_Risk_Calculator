"""Which close a period difference is measured from (user decision 2026-09-21, as
relayed by the main session: "use previous date until has value").

A period figure (Daily, Previous day, 5d, MTD, YTD) is `LTD(a) - LTD(b)`, `b` the
period's reference close. When most of what was open on `b` has no official mark
there, the header and the Blotter strips used to show n/a. The rule here: step the
REFERENCE DATE back one business day at a time (trading calendar, the same
`config/holidays.txt` helpers the period dates use) until a close that has value, at
most `MAX_STEP_BACK_BUSINESS_DAYS` of them, and say so.

What this is NOT. It is a rule about which close a period is measured from, never a
carry-forward of marks: nothing here reads or writes `marks`, no mark is copied or
substituted for any trade on any date, and a trade's own P&L on a date is whatever
`value_book` said (CLAUDE.md hard rules 2 and 3 stand). This module never values
anything itself: the caller hands it already-valued frames through `frame_for`. The
strict engine functions keep their meaning (`ledger.ltd` is NaN when any row is NaN,
`ledger.period_reference_dates` returns the period's own dates, never a stepped one).

"Has value" is the display rule the two screens already apply
(`ui/tabs/header.py::_priced_diff`, `ui/tabs/blotter_pricing.py::_priced_diff_scoped`),
held here once as `diff_split`: a reference close is NOT usable when the trades priced
on `a` but unpriced on `b` outnumber the trades priced at both ends. Everything else is
as before and triggers no step-back: a reference date on which nothing in scope was
open yet is a legitimate zero reference, and a date `a` with nothing priced stays n/a
for its own reason (no earlier reference can help it).

Frames are `value_book`-shaped: columns `trade_id`, `reason` ('' = priced), `pnl_usd`.

Cost: each extra candidate date is one more valuation of the book, so `frame_for` is
called lazily, for a candidate only after the one before it failed; callers pass their
own per-date cached reader (`ui.tabs.blotter_pricing.priced_value_book`).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Callable, FrozenSet, Optional, Tuple

import pandas as pd

from engine.pnl.calendar import _prev_business_day

MAX_STEP_BACK_BUSINESS_DAYS = 10

EMPTY = "EMPTY"                          # nothing in scope on `a`: 0.0, available
OK = "OK"                                # a figure can be formed against this reference
REFERENCE_MISSING = "REFERENCE_MISSING"  # most of what was open on `b` is unpriced there
NOTHING_PRICED = "NOTHING_PRICED"        # nothing on `a` is priced: n/a whatever the reference


@dataclass(frozen=True, eq=False)
class DiffSplit:
    """How the trades of `LTD(a) - LTD(b)` split, exactly as the two screens split them."""
    status: str
    total: int                          # rows on `a`
    blocked_ids: FrozenSet[str]         # priced on `a`, on `b`'s book but unpriced there
    contributing_a_ids: FrozenSet[str]  # priced on `a` and not blocked
    contributing_b_ids: FrozenSet[str]  # priced at both ends
    a_unpriced_ids: FrozenSet[str]
    b_unpriced: pd.DataFrame            # `b`'s unpriced rows, for the caller's own breakdown text

    @property
    def n_blocked(self) -> int:
        return len(self.blocked_ids)

    @property
    def n_open_then(self) -> int:
        return len(self.blocked_ids) + len(self.contributing_b_ids)

    @property
    def usable(self) -> bool:
        """True when the existing rule gives an available figure against this reference."""
        return self.status in (EMPTY, OK)


def diff_split(df_a: pd.DataFrame, df_b: pd.DataFrame) -> DiffSplit:
    """The availability test of `header._priced_diff` / `blotter_pricing._priced_diff_scoped`
    as one pure function. No valuation, no database."""
    none: FrozenSet[str] = frozenset()
    b_unpriced = df_b[df_b["reason"] != ""] if not df_b.empty else df_b
    if len(df_a) == 0:
        return DiffSplit(EMPTY, 0, none, none, none, none, b_unpriced)

    a_priced_ids = frozenset(df_a[df_a["reason"] == ""]["trade_id"])
    a_unpriced_ids = frozenset(df_a[df_a["reason"] != ""]["trade_id"])
    if df_b.empty:
        b_priced_ids, b_unpriced_ids = none, none
    else:
        b_priced_ids = frozenset(df_b[df_b["reason"] == ""]["trade_id"])
        b_unpriced_ids = frozenset(b_unpriced["trade_id"])

    blocked = a_priced_ids & b_unpriced_ids
    contributing_a = a_priced_ids - blocked
    contributing_b = a_priced_ids & b_priced_ids
    if blocked and len(blocked) > len(contributing_b):
        status = REFERENCE_MISSING
    elif not contributing_a:
        status = NOTHING_PRICED
    else:
        status = OK
    return DiffSplit(status, len(df_a), blocked, contributing_a, contributing_b, a_unpriced_ids, b_unpriced)


@dataclass(frozen=True, eq=False)
class SkippedClose:
    """A reference close that was tried and had no value, with the counts the screens'
    own reason sentences are built from."""
    date: str
    n_blocked: int
    n_open_then: int
    unpriced: pd.DataFrame


@dataclass(frozen=True, eq=False)
class ReferenceChoice:
    ref_date: str                       # the period's own reference date, never changed
    ref_date_used: str                  # the close the figure is measured from
    frame: pd.DataFrame                 # the book on `ref_date_used`; the ORIGINAL date's when not found
    split: DiffSplit                    # `diff_split(df_a, frame)`
    skipped: Tuple[SkippedClose, ...]   # newest first; the original reference date leads
    found: bool                         # False: nothing usable within `max_steps` business days
    max_steps: int

    @property
    def stepped_back(self) -> bool:
        return self.found and self.ref_date_used != self.ref_date

    @property
    def note(self) -> str:
        """Visible caption when a step-back happened, '' otherwise:
        "from the 2026-09-11 close: 2026-09-14 has no usable close"."""
        if not self.stepped_back:
            return ""
        earlier = len(self.skipped) - 1
        if earlier == 0:
            return f"from the {self.ref_date_used} close: {self.ref_date} has no usable close"
        days = "business day" if earlier == 1 else "business days"
        return (f"from the {self.ref_date_used} close: {self.ref_date} and the {earlier} {days} "
                f"before it have no usable close")

    @property
    def exhausted_sentence(self) -> str:
        """The one sentence added to the original reason when nothing usable was found."""
        if self.found:
            return ""
        return (f"No earlier close within {self.max_steps} business days has one either "
                f"(checked back to {self.skipped[-1].date}).")


def resolve_reference(df_a: pd.DataFrame, ref_date: str,
                      frame_for: Callable[[str], pd.DataFrame],
                      holidays: Optional[FrozenSet[str]] = None,
                      max_steps: int = MAX_STEP_BACK_BUSINESS_DAYS) -> ReferenceChoice:
    """The close `LTD(a) - LTD(ref)` is measured from. `df_a` is the book on `a`;
    `frame_for(iso_date)` returns the book on a date, scoped the way `df_a` is.

    The period's own `ref_date` is tried first and, when usable, returned untouched
    (no note, nothing else valued). Only a REFERENCE_MISSING close steps back, one
    business day at a time, never forward, stopping at the first usable close and
    after at most `max_steps` earlier business days. When none is usable the choice is
    the ORIGINAL reference date with `found = False`, so the caller's figure is n/a for
    today's reason plus `exhausted_sentence`."""
    if holidays is None:
        from engine.pnl.aggregate import load_holidays  # the calendar `ledger.period_reference_dates` reads
        holidays = load_holidays()

    first_frame = frame_for(ref_date)
    first_split = diff_split(df_a, first_frame)
    if first_split.status != REFERENCE_MISSING:
        return ReferenceChoice(ref_date, ref_date, first_frame, first_split, (), True, max_steps)

    skipped = [SkippedClose(ref_date, first_split.n_blocked, first_split.n_open_then, first_split.b_unpriced)]
    day = dt.date.fromisoformat(ref_date)
    for _ in range(max_steps):
        day = _prev_business_day(day, holidays)
        iso = day.isoformat()
        frame = frame_for(iso)
        split = diff_split(df_a, frame)
        if split.status != REFERENCE_MISSING:
            return ReferenceChoice(ref_date, iso, frame, split, tuple(skipped), True, max_steps)
        skipped.append(SkippedClose(iso, split.n_blocked, split.n_open_then, split.b_unpriced))
    return ReferenceChoice(ref_date, ref_date, first_frame, first_split, tuple(skipped), False, max_steps)


def annotate(entry: dict, choice: ReferenceChoice,
             skipped_reason: Optional[Callable[[SkippedClose], str]] = None) -> dict:
    """A copy of a screen's period entry with the reference-date keys ADDED; every
    existing key keeps its value and meaning (`ref_date`, where present, is untouched):
      - `ref_date_used`: the close the figure is measured from (= the period's own
        reference date when no step-back happened, and when none was found).
      - `ref_note`: the visible caption ('' when no step-back happened).
      - `ref_note_detail`: hover text, the screen's own full reason for each skipped
        date (`skipped_reason(skipped_close)`), one per line.
      - `ref_dates_skipped`: the dates tried and found without value, newest first.
    When nothing usable was found and the entry is n/a, its `reason` is today's reason
    for the original reference date plus `exhausted_sentence`."""
    out = dict(entry)
    out["ref_date_used"] = choice.ref_date_used
    out["ref_note"] = choice.note
    out["ref_dates_skipped"] = tuple(s.date for s in choice.skipped)
    detail = ""
    if choice.stepped_back and skipped_reason is not None:
        detail = "\n".join(skipped_reason(s) for s in choice.skipped)
    out["ref_note_detail"] = detail
    if not choice.found and not out.get("available"):
        reason = str(out.get("reason") or "").rstrip()
        if reason and reason[-1] not in ".!?":
            reason += "."
        out["reason"] = f"{reason} {choice.exhausted_sentence}".strip()
    return out
