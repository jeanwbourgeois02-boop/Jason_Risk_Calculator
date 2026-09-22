"""User-set pay/receive direction for interest rate swaps, persistent across re-uploads.

Why this exists (user, 2026-09-18: "the rates, they're all buy right now; the P&L calc
is correct but the parser sign is not always right"): the blotter export can carry no
direction at all. On the reference sample every INTEREST_RATE_SWAP row has Side = 'Buy',
an unsigned Notional and Quantity, NetInvoice 0 and identical pay/receive leg columns,
including the three swaps the desk holds as receivers -- nothing in the file separates
them. The user's own export (checked 2026-09-18) is the same: no direction marker in any
column. The minus signs in the retired Excel book sat in a hand-typed "Direction" column,
i.e. the user's knowledge, never the export, was the source. So this module is the
PRIMARY source of swap direction, not a fallback: the user sets Pay / Receive by hand
and it sticks. ``data/ingest/blotter.py`` still reads every direction signal an export
*could* carry (brackets / minus on the amount columns, a sell-type Side, pay/receive
wording -- an export layout may add one later) and otherwise defaults to pay fixed,
recording that per swap so the Rates table can flag the swaps still waiting for the
user's choice (``direction_report`` / ``needs_user_choice``). Direction is never
inferred from offsetting notionals, rates or anything else: never guessed.

Storage: ``irs_direction_overrides(trade_id PRIMARY KEY, direction 'PAY'|'RECEIVE',
set_at)``, DDL in ``data/ingest/schema.py::IRS_DIRECTION_DDL`` (created by
``create_schema`` on every startup and defensively by ``set_direction`` here, so an
existing database gets it with no migration step). No foreign key to ``trades`` on
purpose: an upload deletes and rewrites the book, and the override has to survive that.

An override always wins over a file signal: ``blotter.load`` hands the stored overrides
to the parser (so even a row whose own direction signals contradict each other loads,
in the direction the user chose) and calls ``apply_overrides`` once more at the end.

Sign convention (CLAUDE.md): ``trades.quantity`` > 0 = pay fixed, < 0 = receive fixed;
FIXED leg amount = -quantity, FLOAT leg amount = +quantity.

A flipped swap KEEPS ITS HISTORY, by sign reversal, done by the pricer. When a swap
actually turns round (``set_direction``, ``apply_overrides``, ``reverse_flipped``), the
marks engine/rates already wrote for it are not deleted: this module calls
``engine/rates/store.py::reverse_direction_marks``, which multiplies that instrument's
``QL_PRICER`` PV_USD / DV01_USD / CASHFLOW_USD rows by -1 in place on every as_of_date,
leaves PAR_RATE alone, deletes those three types from any other source (nothing
records which direction a BBG_BDH or MANUAL row was entered for) and the trade's
``realised_pnl`` row (re-realised at once from the reversed marks), and deletes rather
than reverses when two trades share one instrument. The function lives in the pricer,
not here (reviewer finding, 2026-09-22): hard rule 2 says nothing reaches ``marks``
that the app's own pricers did not produce, and the ingest layer is not a pricer. Why
reversal is exact, and why reverse rather than delete, is in that function's docstring;
the proof (forward-starting, seasoned with a coupon paid, and expired swaps, reversed
marks equal to what the pricer writes for the flipped trade) is in
tests/test_rates_pricing.py and tests/test_ingest.py
(``test_receiver_marks_are_exactly_minus_the_payers_...``). ``engine.rates.store``
loads QuantLib at import, so it is imported lazily, inside ``_reverse_marks``, and this
module stays QuantLib-free at import time for the parser and the UI.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import sqlite3
from contextlib import contextmanager
from typing import Dict, Iterator, List

from data.ingest.common import NO_DIRECTION_SIGNAL
from data.ingest.schema import IRS_DIRECTION_DDL

log = logging.getLogger(__name__)

TABLE = "irs_direction_overrides"
DIRECTIONS = ("PAY", "RECEIVE")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def ensure_table(conn: sqlite3.Connection) -> None:
    """CREATE TABLE IF NOT EXISTS, for a database that predates the table. Writers only:
    the readers below never create anything, so they work on a read-only connection."""
    conn.execute(IRS_DIRECTION_DDL)


@contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """All-or-nothing on any connection: opens a transaction when none is open (so it
    also holds on an autocommit `isolation_level=None` connection, where `with conn:`
    protects nothing), commits on success, rolls back on any error."""
    if not conn.in_transaction:
        conn.execute("BEGIN")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def _direction(value) -> str:
    text = str(value or "").strip().upper()
    if text not in DIRECTIONS:
        raise ValueError(f"direction must be PAY or RECEIVE, got {value!r}")
    return text


def _apply_one(conn: sqlite3.Connection, trade_id: str, instrument_id: str, quantity, direction: str,
               turn_marks: bool = True) -> bool:
    """Point one IRS trade and its legs in `direction`. Returns True when anything on
    file actually changed, in which case (unless `turn_marks` is False, see
    `reapply_after_load`) what was priced or frozen for the old direction is turned round
    with it (`_reverse_marks`, the pricer's own `reverse_direction_marks`: its marks
    reversed in place on every date, other sources' marks and the `realised_pnl` row
    deleted). An unchanged trade is left exactly as it is: its marks were priced this
    way round."""
    try:
        magnitude = abs(float(quantity))
    except (TypeError, ValueError):
        raise ValueError(f"trade {trade_id}: quantity on file is not a number ({quantity!r})") from None
    if not math.isfinite(magnitude):
        raise ValueError(f"trade {trade_id}: quantity on file is not a number ({quantity!r})")
    signed = magnitude if direction == "PAY" else -magnitude
    targets = {"FIXED": -signed, "FLOAT": signed}
    legs = conn.execute("SELECT leg_type, amount FROM trade_legs WHERE trade_id = ?", (trade_id,)).fetchall()
    changed = quantity != signed or any(lt in targets and amt != targets[lt] for lt, amt in legs)
    if not changed:
        return False
    conn.execute("UPDATE trades SET quantity = ? WHERE trade_id = ?", (signed, trade_id))
    for leg_type, amount in targets.items():
        conn.execute("UPDATE trade_legs SET amount = ? WHERE trade_id = ? AND leg_type = ?",
                     (amount, trade_id, leg_type))
    if turn_marks:
        _reverse_marks(conn, trade_id, instrument_id)
    return True


def _reverse_marks(conn: sqlite3.Connection, trade_id: str, instrument_id: str) -> None:
    """The pricer turns its own marks round (module docstring; hard rule 2). Imported
    here and not at module level because ``engine.rates.store`` loads QuantLib at
    import, which the parser, the upload and the UI must not pay for on every import
    of this module. Runs inside the caller's transaction: it commits nothing."""
    from engine.rates.store import reverse_direction_marks

    reverse_direction_marks(conn, trade_id, instrument_id)


def set_direction(conn: sqlite3.Connection, trade_id: str, direction: str) -> None:
    """Record the user's direction for one swap and apply it, in one transaction:
    upsert the override; `trades.quantity` = +|q| for PAY, -|q| for RECEIVE; FIXED leg =
    -quantity, FLOAT leg = +quantity; and, when that actually flipped the trade, turn
    its priced history round with it (module docstring: the pricer's PV_USD / DV01_USD /
    CASHFLOW_USD rows multiplied by -1 on every date, PAR_RATE untouched, other sources'
    rows of those three types and the trade's `realised_pnl` row deleted), so nothing
    priced for the old direction can be displayed and no history is lost.

    Choosing the direction a swap already has is not a no-op: the override is still
    stored (that is how the user says "yes, this one really is pay fixed", which clears
    `needs_user_choice`); the trade, its legs and every mark are left untouched.

    Raises ValueError for an unknown trade, a trade that is not an IRS, or a direction
    other than 'PAY' / 'RECEIVE' (case and surrounding spaces are forgiven)."""
    direction = _direction(direction)
    trade_id = str(trade_id or "").strip()
    row = conn.execute("SELECT product, instrument_id, quantity FROM trades WHERE trade_id = ?", (trade_id,)).fetchone()
    if row is None:
        raise ValueError(f"no trade {trade_id!r} on file")
    product, instrument_id, quantity = row
    if product != "IRS":
        raise ValueError(f"trade {trade_id} is a {product}, not an interest rate swap")
    with _transaction(conn):
        ensure_table(conn)
        conn.execute(
            f"INSERT INTO {TABLE} (trade_id, direction, set_at) VALUES (?,?,?) "
            "ON CONFLICT(trade_id) DO UPDATE SET direction = excluded.direction, set_at = excluded.set_at",
            (trade_id, direction, _now()))
        _apply_one(conn, trade_id, instrument_id, quantity, direction)


def get_overrides(conn: sqlite3.Connection) -> Dict[str, str]:
    """{trade_id: 'PAY' | 'RECEIVE'} for every stored override, whether or not the trade
    is on file right now. Read-only; {} on a database that has no override table yet."""
    if not _table_exists(conn, TABLE):
        return {}
    return {str(t): str(d) for t, d in conn.execute(f"SELECT trade_id, direction FROM {TABLE}")}


def apply_overrides(conn: sqlite3.Connection) -> int:
    """Re-apply every stored override to the IRS trades on file, in one transaction.
    Returns how many trades on file carry an override (each now faces the way the user
    set, whether or not it needed flipping). A swap this actually flips has its priced
    history turned round with it, as in `set_direction`. A trade whose stored quantity
    is not a number is skipped and logged, never allowed to stop anything."""
    return _apply_overrides(conn, turn_marks=True)


def reapply_after_load(conn: sqlite3.Connection) -> int:
    """`apply_overrides` for the end of `blotter.load`: same trades-and-legs guarantee,
    same count, but it leaves the marks alone. Inside a load the question "did this swap
    turn round?" has exactly one right answer -- its direction before the load against
    its direction after it, overrides included -- and `reverse_flipped` answers it once.
    Letting this step turn marks as well could reverse a swap that the file momentarily
    wrote as pay fixed and the override put straight back, i.e. one that never flipped.
    (In practice the parser has already applied the overrides, so this changes nothing.)"""
    return _apply_overrides(conn, turn_marks=False)


def _apply_overrides(conn: sqlite3.Connection, turn_marks: bool) -> int:
    overrides = get_overrides(conn)
    if not overrides:
        return 0
    applied = 0
    with _transaction(conn):
        for trade_id, direction in overrides.items():
            row = conn.execute("SELECT instrument_id, quantity FROM trades WHERE trade_id = ? AND product = 'IRS'",
                               (trade_id,)).fetchone()
            if row is None:
                continue
            try:
                _apply_one(conn, trade_id, row[0], row[1], direction, turn_marks=turn_marks)
            except ValueError as exc:
                log.warning("direction override not applied: %s", exc)
                continue
            applied += 1
    return applied


# --------------------------------------------------------------------------- extras
def irs_signs(conn: sqlite3.Connection) -> Dict[str, int]:
    """{trade_id: +1 pay fixed | -1 receive fixed} for the IRS trades on file now. Taken
    before a book is rewritten so `reverse_flipped` can tell afterwards which swaps
    turned round. Read-only; a quantity that is not a number is left out."""
    return {str(t): (1 if q > 0 else -1)
            for t, q in conn.execute("SELECT trade_id, quantity FROM trades WHERE product = 'IRS'")
            if isinstance(q, (int, float)) and q != 0}


def reverse_flipped(conn: sqlite3.Connection, before: Dict[str, int]) -> int:
    """After a book rewrite: for every swap that was on file in `before` (`irs_signs`,
    taken before the rewrite) and now faces the other way, turn its priced history round
    exactly as `set_direction` does (`_reverse_marks`). Returns how many swaps that was.
    A swap that is new, gone, or unchanged keeps its marks as they are. Call it ONCE per
    rewrite, on the connection whose marks are the live ones: reversing twice would put
    the old signs back."""
    flipped = 0
    now = irs_signs(conn)
    with _transaction(conn):
        for trade_id, sign in now.items():
            if trade_id in before and before[trade_id] != sign:
                row = conn.execute("SELECT instrument_id FROM trades WHERE trade_id = ?", (trade_id,)).fetchone()
                _reverse_marks(conn, trade_id, row[0])
                flipped += 1
    return flipped


# The old name, from when a flip deleted the marks instead of reversing them.
purge_flipped = reverse_flipped


def clear_direction(conn: sqlite3.Connection, trade_id: str) -> bool:
    """Forget the override for one swap (True if there was one). The trade on file is
    left as it is; the next upload reads its direction from the file again."""
    if not _table_exists(conn, TABLE):
        return False
    with _transaction(conn):
        cur = conn.execute(f"DELETE FROM {TABLE} WHERE trade_id = ?", (str(trade_id or "").strip(),))
    return bool(cur.rowcount and cur.rowcount > 0)


def direction_report(conn: sqlite3.Connection) -> List[dict]:
    """One dict per IRS trade on file, for the Rates table to show each swap's direction
    and flag the ones still waiting for the user's choice. Read-only. Keys:

      trade_id, instrument_id
      direction          'PAY' | 'RECEIVE' ('' when the stored quantity is unusable)
      source             'USER'    an override stored here (or a trade booked by hand)
                         'FILE'    an explicit short marker in the export
                         'DEFAULT' nothing decided it: read as pay fixed
      needs_user_choice  True for 'DEFAULT' rows
      note               a sentence for the row (NO_DIRECTION_SIGNAL for 'DEFAULT')
      set_at             when the override was stored ('' otherwise)

    How 'DEFAULT' is told from the database alone, without guessing: the parser only
    ever writes receive fixed on an explicit signal (brackets / minus, a sell-type Side,
    receive wording) and defaults to PAY, never to RECEIVE. So a swap with no override
    that is on file as RECEIVE was decided by the file; one on file as PAY was either
    defaulted or carried explicit pay wording, which the database cannot tell apart --
    it is flagged, because asking the user to confirm a correct PAY costs a click while
    a wrongly assumed PAY is a P&L of the wrong sign. The reference export and the
    user's own export (checked 2026-09-18) carry no direction marker in any column, so
    in practice every swap without an override is a 'DEFAULT' row. The exact signal the
    parser saw for each swap of an upload is on `ParseResult.irs_directions`."""
    has_table = _table_exists(conn, TABLE)
    join = f"LEFT JOIN {TABLE} o ON o.trade_id = t.trade_id" if has_table else ""
    cols = "o.direction, o.set_at" if has_table else "NULL, NULL"
    out: List[dict] = []
    for trade_id, instrument_id, quantity, trade_source, override, set_at in conn.execute(
            f"SELECT t.trade_id, t.instrument_id, t.quantity, t.source, {cols} FROM trades t {join} "
            "WHERE t.product = 'IRS' ORDER BY t.trade_id"):
        if isinstance(quantity, (int, float)) and quantity != 0:
            direction = "PAY" if quantity > 0 else "RECEIVE"
        else:
            direction = ""
        if override:
            source, note = "USER", f"set by you ({override})"
        elif trade_source == "MANUAL":
            source, note = "USER", "booked by hand"
        elif direction == "RECEIVE":
            source, note = "FILE", "short marker in the file (brackets, minus, sell side or receive wording)"
        else:
            source, note = "DEFAULT", NO_DIRECTION_SIGNAL
        out.append({"trade_id": trade_id, "instrument_id": instrument_id, "direction": direction,
                    "source": source, "needs_user_choice": source == "DEFAULT", "note": note,
                    "set_at": set_at or ""})
    return out


def needs_user_choice(conn: sqlite3.Connection) -> List[str]:
    """Trade ids of the swaps on file whose direction nothing has decided yet."""
    return [r["trade_id"] for r in direction_report(conn) if r["needs_user_choice"]]
