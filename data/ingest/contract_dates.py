"""Bloomberg's own contract dates onto the commodity futures on file (ingest-booking lane).

A commodity future is booked by the parser (or the Manual entry sub-tab) with
``instruments.expiry_date`` = the contract's last trade date, and one ``NOTIONAL`` leg whose
``settle_date`` is that same date. Until Bloomberg's ``FUT_LAST_TRADE_DT`` is on file that
date is contract-master's conservative ESTIMATE (the last weekday of the contract month).
``apply_contract_dates`` replaces the estimate with Bloomberg's stored date
(``data.contracts.static_dates``, the ``contract_static`` table) wherever the two differ:

- ``instruments.expiry_date`` and the ``settle_date`` of every ``NOTIONAL`` leg of every
  trade on the instrument take Bloomberg's date (an option's underlying future written with
  no trade of its own has no legs: its expiry and marks move alone);
- the instrument's marks keyed on ``settle_date`` = the expiry (a future's ``FUTURE_PX``;
  an option's ``FUTURE_PX`` and the PREMIUM / Greeks the options pricer keys the same way)
  are re-keyed from the old date to the new one.

Options on commodity futures (``CMDTY_OPTION``, Phase 5, 2026-09-24) are treated the same
way: contract-master stores an option's own last trade date under its canonical option id
(``'CLZ26C 75 Comdty'``), which is its instrument id. LME forwards (``LME_FWD``) are
perpetual instruments and never touched.

**The one write to ``marks`` outside the Bloomberg lanes.** It moves a key and nothing else:
``value``, ``source`` and ``snapped_at`` of every row are untouched, no row is created, and a
row already on file under the new key wins (the moved duplicate is dropped and counted). It
never produces or changes a price or a Greek, so hard rule 2 ("nothing is written to
``marks`` that Bloomberg or the app's own pricers did not produce") holds: every value stays
Bloomberg's or the pricer's own.

Who calls it: bbg-live, after storing the dates on "Pull Bloomberg now" and before it asks
for any futures price (so the price is asked for at the right date), and
``upload.import_blotter`` after every upload (a re-upload writes the parser's estimate again).
It asks Bloomberg nothing (hard rule 8). Each instrument's change is one transaction; the
``trade_legs`` update fires the Bloomberg library's dirty trigger, so the library re-syncs
with the new dates on its next read.
"""
from __future__ import annotations

import sqlite3
from typing import Dict, List

from data.contracts import load_roots, static_dates
from data.contracts.static import TABLE as STATIC_TABLE
from data.contracts.tickers import parse_option_ticker


def _empty() -> dict:
    return {"checked": 0, "updated": [], "missing_dates": []}


def _has_static_table(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                        (STATIC_TABLE,)).fetchone() is not None


# The instruments whose expiry Bloomberg's stored dates replace: commodity futures (an
# option's underlying written with no trade of its own included) and, since Phase 5
# (2026-09-24), options on commodity futures, whose stored last trade date is the option's
# own expiry (contract-master keys it by the canonical option id, 'CLZ26C 75 Comdty').
ASSET_CLASSES = ("FUTURE", "CMDTY_OPTION")


def _commodity_contracts(conn: sqlite3.Connection) -> List[tuple]:
    """(instrument_id, expiry_date) of every FUTURE or CMDTY_OPTION whose base_ccy is a
    contract root. LME forwards (LME_FWD, perpetual instruments) are never in it."""
    roots = load_roots()
    rows = conn.execute("SELECT instrument_id, base_ccy, expiry_date FROM instruments "
                        f"WHERE asset_class IN ({','.join('?' for _ in ASSET_CLASSES)}) ORDER BY instrument_id",
                        ASSET_CLASSES).fetchall()
    return [(iid, expiry) for iid, base, expiry in rows if base in roots]


def _rekey_marks(conn: sqlite3.Connection, instrument_id: str, old_dates: List[str], new_date: str) -> Dict[str, int]:
    """Move the instrument's marks from each old settle_date to `new_date`; a row already under
    the new key wins and the moved one is dropped. Values never change.

    Every mark type keyed on the expiry moves: a future's FUTURE_PX, and an option's FUTURE_PX
    (Bloomberg's price of the option) with the PREMIUM / Greeks the options pricer keys on the
    same expiry. Left behind, an option's Greeks under the old key would sit beside the ones
    priced at Bloomberg's date for the same day, and the delta query, which does not join on
    settle_date, would count both."""
    dropped = rekeyed = 0
    for old in old_dates:
        dropped += conn.execute(
            "DELETE FROM marks WHERE instrument_id = ? AND settle_date = ? AND EXISTS ("
            "SELECT 1 FROM marks n WHERE n.as_of_date = marks.as_of_date AND n.instrument_id = marks.instrument_id "
            "AND n.mark_type = marks.mark_type AND n.source = marks.source AND n.settle_date = ?)",
            (instrument_id, old, new_date)).rowcount
        rekeyed += conn.execute(
            "UPDATE marks SET settle_date = ? WHERE instrument_id = ? AND settle_date = ?",
            (new_date, instrument_id, old)).rowcount
    return {"marks_rekeyed": rekeyed, "marks_dropped": dropped}


def apply_contract_dates(conn: sqlite3.Connection) -> dict:
    """Write Bloomberg's stored last trade date onto every commodity future and option on a
    commodity future that has one.

    Returns, exactly::

        {'checked': n,              # contracts looked at (FUTURE / CMDTY_OPTION, base_ccy a contract root)
         'updated': [{'instrument_id', 'expiry_before', 'expiry_after',
                      'legs', 'marks_rekeyed', 'marks_dropped'}],
         'missing_dates': [instrument_id, ...]}   # no Bloomberg dates stored yet

    An instrument is updated when Bloomberg's date differs from its expiry_date or from the
    settle_date of any of its trades' NOTIONAL legs. An instrument with no trade (an option's
    underlying future the upload wrote on its own) moves its expiry and marks, with 0 legs.
    A database without the contract_static table returns
    ``{'checked': 0, 'updated': [], 'missing_dates': []}`` and never raises.
    """
    if not _has_static_table(conn):
        return _empty()
    out = _empty()
    for instrument_id, expiry_before in _commodity_contracts(conn):
        out["checked"] += 1
        dates = static_dates(conn, instrument_id)
        new_date = (dates or {}).get("last_trade_date") or ""
        if not new_date:
            out["missing_dates"].append(instrument_id)
            continue
        leg_dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT l.settle_date FROM trade_legs l JOIN trades t USING (trade_id) "
            "WHERE t.instrument_id = ? AND l.leg_type = 'NOTIONAL'", (instrument_id,))]
        old_dates = sorted({expiry_before, *leg_dates} - {new_date})
        if not old_dates:
            continue
        with conn:
            conn.execute("UPDATE instruments SET expiry_date = ? WHERE instrument_id = ?", (new_date, instrument_id))
            legs = conn.execute(
                "UPDATE trade_legs SET settle_date = ? WHERE leg_type = 'NOTIONAL' AND settle_date != ? "
                "AND trade_id IN (SELECT trade_id FROM trades WHERE instrument_id = ?)",
                (new_date, new_date, instrument_id)).rowcount
            moved = _rekey_marks(conn, instrument_id, old_dates, new_date)
        out["updated"].append({"instrument_id": instrument_id, "expiry_before": expiry_before,
                               "expiry_after": new_date, "legs": legs, **moved})
    return out


def summary_sentence(result: dict) -> str:
    """One plain sentence for the upload summary; '' when nothing changed. Futures and options
    on futures are counted apart ('2 future(s) and 1 option(s) on futures moved ...'), an option
    told by its canonical option id ('CLZ26C 75 Comdty'), so the result's shape is unchanged."""
    updated = result.get("updated") or []
    if not updated:
        return ""
    n_options = sum(1 for u in updated if parse_option_ticker(u["instrument_id"]) is not None)
    n_futures = len(updated) - n_options
    what = " and ".join(part for part in (f"{n_futures} future(s)" if n_futures else "",
                                          f"{n_options} option(s) on futures" if n_options else "") if part)
    moves = ", ".join(f"{u['instrument_id']} {u['expiry_before']} -> {u['expiry_after']}" for u in updated[:5])
    more = f" (+{len(updated) - 5} more)" if len(updated) > 5 else ""
    return (f"Contract dates: {what} moved to Bloomberg's last trade date on file "
            f"({moves}{more}); their legs and prices on file moved with them.")
