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
import re
from dataclasses import dataclass
from typing import Callable, FrozenSet, Optional, Tuple

import pandas as pd

from engine.pnl.calendar import _prev_business_day

# User, 2026-09-21: "if the previous day is also blank then what - let it backfill up to 5 days".
MAX_STEP_BACK_BUSINESS_DAYS = 5

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
    filled: Tuple[Tuple[str, int], ...] = ()   # (earlier close, trades taken from it), newest first

    @property
    def stepped_back(self) -> bool:
        return self.found and self.ref_date_used != self.ref_date

    @property
    def note(self) -> str:
        """Visible caption when a step-back happened, '' otherwise:
        "from the 2026-09-11 close: 2026-09-14 has no usable close"."""
        if not self.stepped_back:
            return self.fill_note
        earlier = len(self.skipped) - 1
        if earlier == 0:
            return f"from the {self.ref_date_used} close: {self.ref_date} has no usable close"
        days = "business day" if earlier == 1 else "business days"
        return (f"from the {self.ref_date_used} close: {self.ref_date} and the {earlier} {days} "
                f"before it have no usable close")

    @property
    def fill_note(self) -> str:
        """"12 trades with no price on 2026-09-14 measured from their last earlier close (back to
        2026-09-10)" when single trades were filled, '' otherwise."""
        if not self.filled:
            return ""
        n = sum(count for _day, count in self.filled)
        return (f"{n} trade{'s' if n != 1 else ''} with no price on {self.ref_date_used} measured from "
                f"{'their' if n != 1 else 'its'} last earlier close (back to {self.filled[-1][0]})")

    @property
    def exhausted_sentence(self) -> str:
        """The one sentence added to the original reason when nothing usable was found."""
        if self.found:
            return ""
        return (f"No earlier close within {self.max_steps} business days has one either "
                f"(checked back to {self.skipped[-1].date}).")


# ------------------------------------------------------------------ the fill (2026-09-21)
# User decision 2026-09-21: "there should be a fill when bloomberg doesnt have the data"
# (after "use previous date until has value", "let it backfill up to 5 days" and "ive told
# you like 5 times about the fill function"). Until then only a period's REFERENCE side was
# filled (`fill_single_trades`), so a trade with no price on the date being looked at was
# still blank in LTD, on the chart and in the Blotter.
_VALUATION_COLUMNS = ("mark", "mark_date", "mark_source", "spot", "spot_source", "pnl_local", "pnl_usd",
                      "pnl_spot_usd", "pnl_carry_usd")
_NEVER_FILLED = (" is not a number (", "could not be valued")
_FILL_NOTE_RE = re.compile(r"^no price on (\d{4}-\d{2}-\d{2}): value of the (\d{4}-\d{2}-\d{2}) close")


def filled_from(note) -> str:
    """The close a filled row's value comes from ('' for a row priced on its own date)."""
    m = _FILL_NOTE_RE.match(note) if isinstance(note, str) else None
    return m.group(2) if m else ""


def filled_days(frame: pd.DataFrame, trade_ids=None) -> Tuple[Tuple[str, int], ...]:
    """((earlier close, trades valued from it), ...) newest first, over `frame`'s filled rows
    (only those of `trade_ids` when given)."""
    if frame is None or frame.empty or "note" not in frame.columns:
        return ()
    rows = frame if trade_ids is None else frame[frame["trade_id"].isin(trade_ids)]
    days = [d for d in (filled_from(n) for n in rows["note"]) if d]
    return tuple(sorted(((d, days.count(d)) for d in set(days)), reverse=True))


def fill_caption(frame: pd.DataFrame, as_of: str, trade_ids=None) -> str:
    """"12 trades with no price on 2026-09-18 valued at their last earlier close (back to
    2026-09-15)", '' when nothing on `frame` was filled."""
    filled = filled_days(frame, trade_ids)
    if not filled:
        return ""
    n = sum(count for _day, count in filled)
    return (f"{n} trade{'s' if n != 1 else ''} with no price on {as_of} valued at "
            f"{'their' if n != 1 else 'its'} last earlier close (back to {filled[-1][0]})")


def fill_book(frame: pd.DataFrame, as_of: str, rows_for: Callable[[str, FrozenSet[str]], pd.DataFrame],
              holidays: Optional[FrozenSet[str]] = None,
              max_steps: int = MAX_STEP_BACK_BUSINESS_DAYS) -> Tuple[pd.DataFrame, Tuple[Tuple[str, int], ...]]:
    """(frame, filled): the book on `as_of` with every trade that has no price there given
    ITS OWN valuation from the last earlier business day on which it has one, at most
    `max_steps` back. `rows_for(iso_date, trade_ids)` values those trades on that day,
    UNFILLED (`value_book(conn, iso, trade_ids)`), so a value is never carried further than
    `max_steps` business days. `filled` is ((earlier close, trades), ...), newest first.

    The filled row keeps what it is on `as_of` (status, product, quantity, fill) and takes
    the earlier day's valuation columns whole -- mark, spot and P&L of ONE close, never a
    mix of two dates; `reason` becomes '' so it sums like any priced row, and `note` opens
    with "no price on <as_of>: value of the <day> close" followed by why it had none, so it
    is never silent. Nothing is written to `marks`: the Market data tab still shows the
    mark as missing. A trade with no earlier price in reach stays blank with its reason."""
    if frame is None or frame.empty:
        return frame, ()
    if holidays is None:
        from engine.pnl.aggregate import load_holidays
        holidays = load_holidays()
    # A stored value that is not a number is a data error to fix, not data Bloomberg does not
    # have: that row stays blank and loud (`valuation._BadValue`, `_guarded_row`).
    remaining = {tid for tid, reason in zip(frame["trade_id"], frame["reason"])
                 if reason and not any(s in str(reason) for s in _NEVER_FILLED)}
    if not remaining:
        return frame, ()
    frame = frame.copy()
    counts = {}
    day = dt.date.fromisoformat(as_of)
    for _ in range(max_steps):
        if not remaining:
            break
        day = _prev_business_day(day, holidays)
        try:
            earlier = rows_for(day.isoformat(), frozenset(remaining))
        except Exception:  # noqa: BLE001 -- an earlier day that cannot be valued fills nothing
            break
        if earlier is None or earlier.empty:
            continue
        usable = earlier[earlier["trade_id"].isin(remaining) & (earlier["reason"] == "")]
        for row in usable.itertuples(index=False):
            at = frame.index[frame["trade_id"] == row.trade_id][0]
            why = str(frame.at[at, "reason"])
            for column in _VALUATION_COLUMNS:
                frame.at[at, column] = getattr(row, column)
            frame.at[at, "reason"] = ""
            frame.at[at, "note"] = f"no price on {as_of}: value of the {day.isoformat()} close ({why})"
            remaining.discard(row.trade_id)
            counts[day.isoformat()] = counts.get(day.isoformat(), 0) + 1
    return frame, tuple(sorted(counts.items(), reverse=True))


def fill_single_trades(df_a: pd.DataFrame, ref_date: str, frame: pd.DataFrame,
                       frame_for: Callable[[str], pd.DataFrame], holidays: FrozenSet[str],
                       max_steps: int = MAX_STEP_BACK_BUSINESS_DAYS):
    """(frame, filled): `frame` (the book on `ref_date`) with every trade that is priced on `a`
    but has no price on `ref_date` given ITS OWN row from the last earlier business day on
    which it is priced, at most `max_steps` back (user, 2026-09-21: "use previous date until has
    value", "let it backfill up to 5 days" -- per trade, not only when the whole close is
    blank). The row is that day's own valuation of the trade: no mark is copied or written,
    and a trade with no earlier price stays left out. An earlier day that cannot be valued
    ends the walk."""
    remaining = set(diff_split(df_a, frame).blocked_ids)
    filled = []
    day = dt.date.fromisoformat(ref_date)
    for _ in range(max_steps):
        if not remaining:
            break
        day = _prev_business_day(day, holidays)
        try:
            earlier = frame_for(day.isoformat())
        except Exception:  # noqa: BLE001 -- an earlier day that cannot be valued fills nothing
            break
        if earlier is None or earlier.empty:
            continue
        usable = earlier[earlier["trade_id"].isin(remaining) & (earlier["reason"] == "")]
        usable = usable[[not filled_from(n) for n in usable["note"]]]   # a filled row is never carried further
        if usable.empty:
            continue
        frame = pd.concat([frame[~frame["trade_id"].isin(usable["trade_id"])], usable], ignore_index=True)
        filled.append((day.isoformat(), len(usable)))
        remaining -= set(usable["trade_id"])
    return frame, tuple(filled)


def resolve_reference(df_a: pd.DataFrame, ref_date: str,
                      frame_for: Callable[[str], pd.DataFrame],
                      holidays: Optional[FrozenSet[str]] = None,
                      max_steps: int = MAX_STEP_BACK_BUSINESS_DAYS,
                      frames_filled: bool = False) -> ReferenceChoice:
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
    if frames_filled and first_split.status != REFERENCE_MISSING:
        # `frame_for` already applies the fill (`fill_book`, the screens' shared reader): the
        # trades filled on this close are read off the frame, never walked back a second time
        return ReferenceChoice(ref_date, ref_date, first_frame, first_split, (), True, max_steps,
                               filled_days(first_frame, first_split.contributing_b_ids))
    if first_split.status != REFERENCE_MISSING:
        if not first_split.blocked_ids:
            return ReferenceChoice(ref_date, ref_date, first_frame, first_split, (), True, max_steps)
        # a usable close with a few trades unpriced on it: each takes its own last earlier price
        frame, filled = fill_single_trades(df_a, ref_date, first_frame, frame_for, holidays, max_steps)
        return ReferenceChoice(ref_date, ref_date, frame, diff_split(df_a, frame), (), True, max_steps, filled)

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
    elif choice.filled and "note" in choice.frame.columns:
        # the filled trades' own notes: which close each value is from, and why it had none
        notes = [f"{r.trade_id}: {r.note}" for r in choice.frame.itertuples()
                 if filled_from(r.note) and r.trade_id in choice.split.contributing_b_ids]
        detail = "\n".join(notes[:40] + ([f"and {len(notes) - 40} more"] if len(notes) > 40 else []))
    out["ref_note_detail"] = detail
    if not choice.found and not out.get("available"):
        reason = str(out.get("reason") or "").rstrip()
        if reason and reason[-1] not in ".!?":
            reason += "."
        out["reason"] = f"{reason} {choice.exhausted_sentence}".strip()
    return out
