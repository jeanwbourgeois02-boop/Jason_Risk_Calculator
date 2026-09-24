"""Past-close option pricing and the stamp on a pricer mark (options-pricer, 2026-09-22).

User, 2026-09-22: "options daily pnl 0, that cannot be right, everything is moving", then
"even on the bbg machine, it seems the options are not priced live. I expect every time I
pull bloomberg now, the options are repriced with the latest data, and that the latest
data is also logged / overwriting previous marks". On the Bloomberg PC the QL_OPTIONS_PRICER
marks existed for the latest day only, all stamped a flat 15:00 although the pull ran at
23:08 New York; the previous close had none, so the near-marks rule carried today's
PREMIUM back and Daily was 0 for every option.

Covered here: `engine.options.store.price_close` (a past close, strictly from that day's
own inputs, for the backfill to call per day), the live stamp (`live_stamp`: the actual
pricing time in New York) against the close stamp (`close_stamp`: 15:00 New York of the
day), and that a re-pull replaces the day's marks in place. Fixtures come from
tests/test_options_pricing.py; same skip-if-QuantLib-absent convention.
"""
from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import pytest

from tests.test_options_pricing import (
    AS_OF, EURUSD, EXPIRY, NOTIONAL, PREMIUM_FILL, SECOND_AS_OF, SEVEN_TYPES, SPOT, STRIKE,
    _new_db, _price_on_two_dates, _pricer_marks, _seed_bare_pair, _seed_ois_curves_for_pair,
    _seed_option_trade, _seed_pair_spot, _seed_vol, needs_quantlib,
)

NY = ZoneInfo("America/New_York")
DAY = AS_OF                 # 2026-08-21, the close being rebuilt
LATER = SECOND_AS_OF        # 2026-08-24, a later day whose inputs must never leak into DAY
INSTRUMENT = "EURUSD092326C-197727826"   # _seed_option_trade's default instrument (premium-adjusted pair)


def _rows(conn, instrument_id, day):
    return conn.execute(
        "SELECT mark_type, value, snapped_at FROM marks WHERE instrument_id = ? AND as_of_date = ? "
        "AND source = 'QL_OPTIONS_PRICER' ORDER BY mark_type", (instrument_id, day)).fetchall()


def _official(conn, instrument_id, day, mark_type):
    row = conn.execute("SELECT value FROM marks_official WHERE instrument_id = ? AND as_of_date = ? "
                       "AND mark_type = ?", (instrument_id, day, mark_type)).fetchone()
    return row[0] if row else None


def _set_spot(conn, day, value, pair=EURUSD):
    conn.execute("UPDATE marks SET value = ? WHERE instrument_id = ? AND as_of_date = ? AND mark_type = 'SPOT'",
                 (value, pair, day))
    conn.commit()


def _seed_day(conn, day=DAY, spot=SPOT):
    """SPOT + OIS curves + a manual vol for `day`, all keyed on that day alone."""
    _seed_pair_spot(conn, as_of=day, spot=spot)
    _seed_vol(conn, as_of=day)


def _insert_realised(conn, trade_id, instrument_id, spot_as_of_date, pnl_usd=-12_345.0):
    conn.execute(
        "INSERT INTO realised_pnl (trade_id, instrument_id, product, currency, settle_date, local_amount, "
        "usd_entry_amount, mark_type, spot_usd_per_local, spot_as_of_date, spot_source, pnl_usd, frozen_at, note) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, instrument_id, "FX_OPTION", "EUR", EXPIRY, NOTIONAL, NOTIONAL * PREMIUM_FILL * 1.17, "PREMIUM",
         0.0045, spot_as_of_date, "QL_OPTIONS_PRICER", pnl_usd, "2026-09-24T09:00:00+00:00",
         f"premium dated {spot_as_of_date}"))
    conn.commit()


# --------------------------------------------------------------------------- price_close: the day's own inputs

@needs_quantlib
def test_price_close_prices_the_days_book_from_that_days_inputs_and_stamps_the_close():
    from engine.options import store

    conn = _new_db()
    _seed_day(conn)
    _seed_option_trade(conn)

    out = store.price_close(conn, DAY)

    assert out == {"day": DAY, "priced": 1, "skipped": [], "closed_out": [], "futures_options_priced": 0}
    rows = _rows(conn, INSTRUMENT, DAY)
    assert {r[0] for r in rows} == SEVEN_TYPES
    assert {r[2] for r in rows} == {f"{DAY}T15:00:00-04:00"}          # the close, offset resolved for August
    assert store.close_stamp(DAY) == f"{DAY}T15:00:00-04:00"
    # The value is the model price of DAY's inputs, the same the live path gives for that date.
    twin = _new_db()
    _seed_day(twin)
    _seed_option_trade(twin)
    live = store.price_and_store(twin, DAY, "T1")
    assert live.priced
    assert _official(conn, INSTRUMENT, DAY, "PREMIUM") == pytest.approx(live.result.premium, rel=1e-12)
    assert _official(conn, INSTRUMENT, DAY, "DELTA") == pytest.approx(live.result.delta, rel=1e-12)


@needs_quantlib
def test_price_close_reads_only_that_days_inputs_and_names_the_day_and_the_missing_one():
    """Everything is on file for LATER and nothing for DAY: DAY's close must not borrow a
    later day's spot, curve or vol. Each input is then added for DAY in turn and the skip
    reason moves to the next missing one."""
    from engine.options import store

    conn = _new_db()
    _seed_option_trade(conn)
    _seed_day(conn, day=LATER)

    out = store.price_close(conn, DAY)
    assert out["priced"] == 0
    assert out["skipped"] == [{"trade_id": "T1", "reason": f"{DAY} close: no SPOT mark"}]
    assert _rows(conn, INSTRUMENT, DAY) == []

    _seed_bare_pair(conn, EURUSD, SPOT, as_of=DAY)          # DAY's spot, no curves, no vol
    out = store.price_close(conn, DAY)
    assert out["skipped"] == [{"trade_id": "T1", "reason": f"{DAY} close: no curve/rate EUR"}]
    assert _rows(conn, INSTRUMENT, DAY) == []

    _seed_ois_curves_for_pair(conn, DAY, EURUSD)
    conn.commit()
    out = store.price_close(conn, DAY)
    assert out["skipped"] == [{"trade_id": "T1", "reason": f"{DAY} close: no vol"}]
    assert _rows(conn, INSTRUMENT, DAY) == []

    _seed_vol(conn, as_of=DAY)
    out = store.price_close(conn, DAY)
    assert out == {"day": DAY, "priced": 1, "skipped": [], "closed_out": [], "futures_options_priced": 0}
    # Nothing was ever written for LATER: price_close(DAY) touches DAY alone.
    assert _rows(conn, INSTRUMENT, LATER) == []


@needs_quantlib
def test_price_close_leaves_out_a_trade_not_yet_dealt_and_one_already_expired():
    from engine.options import store

    conn = _new_db()
    _seed_day(conn)
    _seed_option_trade(conn, trade_id="IN_BOOK")
    dealt_later = _seed_option_trade(conn, trade_id="LATER_DEAL", instrument_id="EURUSD092326P-1")
    conn.execute("UPDATE trades SET trade_date = ? WHERE trade_id = 'LATER_DEAL'", (LATER,))
    expired = _seed_option_trade(conn, trade_id="GONE", instrument_id="EURUSD082026C-1", expiry="2026-08-20")
    conn.commit()

    out = store.price_close(conn, DAY)

    assert out == {"day": DAY, "priced": 1, "skipped": [], "closed_out": [], "futures_options_priced": 0}     # neither priced nor listed
    assert len(_rows(conn, INSTRUMENT, DAY)) == 7
    assert _rows(conn, dealt_later, DAY) == []
    assert _rows(conn, expired, DAY) == []
    assert conn.execute("SELECT COUNT(*) FROM marks WHERE source = 'QL_OPTIONS_PRICER' "
                        "AND instrument_id IN (?, ?)", (dealt_later, expired)).fetchone()[0] == 0


@needs_quantlib
def test_price_close_on_the_expiry_day_writes_the_payoff_drops_a_stale_frozen_row_and_respects_a_final_one():
    from engine.options import store

    conn = _new_db()
    _seed_day(conn, day=EXPIRY, spot=SPOT)             # SPOT 11.06 > STRIKE 11.0584: the call pays off
    _seed_option_trade(conn)
    _insert_realised(conn, "T1", INSTRUMENT, spot_as_of_date="2026-09-22")   # frozen from a T-1 model premium

    out = store.price_close(conn, EXPIRY)

    assert out == {"day": EXPIRY, "priced": 1, "skipped": [], "closed_out": [], "futures_options_priced": 0}
    assert _official(conn, INSTRUMENT, EXPIRY, "PREMIUM") == pytest.approx((SPOT - STRIKE) / SPOT)
    assert _official(conn, INSTRUMENT, EXPIRY, "DELTA") == 1.0
    for greek in ("GAMMA", "THETA", "VEGA", "RHO"):
        assert _official(conn, INSTRUMENT, EXPIRY, greek) == 0.0
    assert {r[2] for r in _rows(conn, INSTRUMENT, EXPIRY)} == {f"{EXPIRY}T15:00:00-04:00"}
    # The stale row went in the same transaction, so the ledger's next pass freezes from the payoff.
    assert conn.execute("SELECT COUNT(*) FROM realised_pnl").fetchone()[0] == 0

    # Once frozen FROM the expiry-dated mark, a re-run leaves mark and row alone (W-2).
    _insert_realised(conn, "T1", INSTRUMENT, spot_as_of_date=EXPIRY)
    _set_spot(conn, EXPIRY, STRIKE - 0.5)               # the spot on file changes afterwards
    out = store.price_close(conn, EXPIRY)
    assert out["priced"] == 0
    assert len(out["skipped"]) == 1 and out["skipped"][0]["trade_id"] == "T1"
    assert out["skipped"][0]["reason"].startswith(f"{EXPIRY} close: expiry day: the payoff is already frozen")
    assert _official(conn, INSTRUMENT, EXPIRY, "PREMIUM") == pytest.approx((SPOT - STRIKE) / SPOT)
    assert conn.execute("SELECT COUNT(*) FROM realised_pnl").fetchone()[0] == 1


@needs_quantlib
def test_price_close_rerun_overwrites_the_same_keys_and_never_adds_a_row():
    from engine.options import store

    conn = _new_db()
    _seed_day(conn)
    _seed_option_trade(conn)

    assert store.price_close(conn, DAY)["priced"] == 1
    first = _official(conn, INSTRUMENT, DAY, "PREMIUM")
    _set_spot(conn, DAY, SPOT * 1.02)
    assert store.price_close(conn, DAY)["priced"] == 1
    second = _official(conn, INSTRUMENT, DAY, "PREMIUM")

    assert second > first                                     # a call, spot up
    assert len(_rows(conn, INSTRUMENT, DAY)) == 7             # seven keys, still one row each
    assert conn.execute("SELECT COUNT(*) FROM marks WHERE instrument_id = ? AND as_of_date = ? "
                        "AND mark_type = 'PREMIUM'", (INSTRUMENT, DAY)).fetchone()[0] == 1
    # Idempotent: the same inputs again change nothing.
    assert store.price_close(conn, DAY)["priced"] == 1
    assert _official(conn, INSTRUMENT, DAY, "PREMIUM") == second


@needs_quantlib
def test_price_close_never_raises(monkeypatch):
    from engine.options import store

    conn = _new_db()
    _seed_day(conn)
    _seed_option_trade(conn)

    out = store.price_close(conn, "not-a-date")
    assert out["day"] == "not-a-date" and out["priced"] == 0 and "ValueError" in out["error"]

    def boom(*_args, **_kwargs):
        raise RuntimeError("vendored pricer fell over")

    monkeypatch.setattr(store, "_dispatch", boom)             # one trade's pricer blowing up
    out = store.price_close(conn, DAY)
    assert "error" not in out and out["priced"] == 0
    assert out["skipped"] == [{"trade_id": "T1",
                               "reason": f"{DAY} close: pricer error: RuntimeError('vendored pricer fell over')"}]
    assert _rows(conn, INSTRUMENT, DAY) == []

    monkeypatch.setattr(store, "_read_option_trade", boom)    # something outside one trade's pricing
    out = store.price_close(conn, DAY)
    assert out["priced"] == 0 and out["skipped"] == [] and "RuntimeError" in out["error"]


def test_price_close_is_importable_without_pricing_anything():
    """No QuantLib needed to import or to run over an empty book."""
    from engine.options import store

    conn = _new_db()
    assert store.price_close(conn, DAY) == {"day": DAY, "priced": 0, "skipped": [], "closed_out": [], "futures_options_priced": 0}


# --------------------------------------------------------------------------- recalc_on_file: no Bloomberg, the logged data

@needs_quantlib
def test_recalc_on_file_rebuilds_every_past_day_with_inputs_and_prices_as_of_from_what_is_there(monkeypatch):
    """User, 2026-09-22: "pull bbg now should recalc options too, using log data if no bbg
    access". Two past days have SPOT / curves / vol on file, the as-of day has nothing yet:
    both past days get close-stamped marks from their own data, the as-of day skips with
    its reason (the near-marks rule carries the last close forward at read time), and a
    second run changes nothing."""
    from engine.options import store

    conn = _new_db()
    _seed_option_trade(conn)                              # dealt 2026-08-18
    _seed_day(conn, day=DAY)                              # 2026-08-21
    _seed_day(conn, day=LATER, spot=SPOT * 1.01)          # 2026-08-24
    as_of = "2026-08-25"
    monkeypatch.setattr(store, "_now_ny", lambda: datetime.datetime(2026, 8, 25, 9, 30, 0, tzinfo=NY))

    out = store.recalc_on_file(conn, as_of)

    assert out["as_of"] == as_of and out["since"] == "2026-08-18"
    assert [d["day"] for d in out["days"]] == [DAY, LATER, as_of]
    assert [d["priced"] for d in out["days"]] == [1, 1, 0]
    assert out["days"][2]["skipped"] == [{"trade_id": "T1", "reason": "no SPOT mark"}]
    assert out["priced"] == 2 and out["skipped"] == 1 and "error" not in out
    assert {r[2] for r in _rows(conn, INSTRUMENT, DAY)} == {f"{DAY}T15:00:00-04:00"}
    assert {r[2] for r in _rows(conn, INSTRUMENT, LATER)} == {f"{LATER}T15:00:00-04:00"}
    assert _rows(conn, INSTRUMENT, as_of) == []
    assert _official(conn, INSTRUMENT, LATER, "PREMIUM") > _official(conn, INSTRUMENT, DAY, "PREMIUM")   # a call, spot up

    again = store.recalc_on_file(conn, as_of)
    assert (again["priced"], again["skipped"]) == (2, 1)
    assert len(_rows(conn, INSTRUMENT, DAY)) == 7 and len(_rows(conn, INSTRUMENT, LATER)) == 7


@needs_quantlib
def test_recalc_on_file_visits_only_days_with_an_option_pairs_spot_from_since_on(monkeypatch):
    from engine.options import store

    conn = _new_db()
    _seed_option_trade(conn)
    _seed_day(conn, day="2026-08-14")                     # before the trade was dealt: not that option's day
    _seed_day(conn, day=DAY)
    _seed_day(conn, day=LATER)
    _seed_pair_spot(conn, as_of="2026-08-20", pair="USDJPY", spot=150.0)   # a pair no option is written on
    monkeypatch.setattr(store, "_now_ny", lambda: datetime.datetime(2026, 8, 25, 9, 30, 0, tzinfo=NY))

    out = store.recalc_on_file(conn, "2026-08-25")
    assert [d["day"] for d in out["days"]] == [DAY, LATER, "2026-08-25"]   # default since = first option trade_date
    assert _rows(conn, INSTRUMENT, "2026-08-14") == []

    out = store.recalc_on_file(conn, "2026-08-25", since=LATER)
    assert [d["day"] for d in out["days"]] == [LATER, "2026-08-25"] and out["since"] == LATER

    # As-of ON a day with inputs: that day is priced live (pricing-time stamp), not as a close.
    out = store.recalc_on_file(conn, LATER)
    assert [d["day"] for d in out["days"]] == [DAY, LATER] and out["days"][1]["priced"] == 1
    assert {r[2] for r in _rows(conn, INSTRUMENT, LATER)} == {"2026-08-25T09:30:00-04:00"}


def test_recalc_on_file_never_raises_and_is_empty_on_an_empty_book(monkeypatch):
    from engine.options import store

    conn = _new_db()
    out = store.recalc_on_file(conn, DAY)
    assert out == {"as_of": DAY, "since": DAY, "days": [{"day": DAY, "priced": 0, "skipped": [], "closed_out": [], "futures_options_priced": 0}],
                   "priced": 0, "skipped": 0, "closed_out": 0, "futures_options_priced": 0}

    out = store.recalc_on_file(conn, "not-a-date")
    assert out["days"] == [] and "ValueError" in out["error"]

    def boom(*_args, **_kwargs):
        raise RuntimeError("no such table")

    monkeypatch.setattr(store, "price_all_and_store", boom)
    out = store.recalc_on_file(conn, DAY)
    assert out["priced"] == 0 and "RuntimeError" in out["error"]


# --------------------------------------------------------------------------- the stamp on a mark

@needs_quantlib
def test_a_live_run_stamps_the_actual_pricing_time_in_new_york_not_the_close():
    from engine.options import store

    conn = _new_db()
    _seed_day(conn)
    _seed_option_trade(conn)

    before = datetime.datetime.now(NY).replace(microsecond=0)
    outcomes = store.price_all_and_store(conn, DAY)
    after = datetime.datetime.now(NY)

    assert [o.priced for o in outcomes] == [True]
    stamps = {r[2] for r in _rows(conn, INSTRUMENT, DAY)}
    assert len(stamps) == 1                                   # one moment for all seven rows of the trade
    stamp = datetime.datetime.fromisoformat(stamps.pop())
    assert before <= stamp <= after                           # the moment it was priced ...
    assert stamp.utcoffset() == after.utcoffset()             # ... with New York's offset
    assert stamp != datetime.datetime.fromisoformat(store.close_stamp(DAY))

    # A caller that knows the moment passes it; the row carries it exactly.
    store.price_and_store(conn, DAY, "T1", snapped="2026-08-21T22:31:12-04:00")
    assert {r[2] for r in _rows(conn, INSTRUMENT, DAY)} == {"2026-08-21T22:31:12-04:00"}


@needs_quantlib
def test_every_pull_reprices_with_the_latest_data_and_overwrites_the_days_marks(monkeypatch):
    """User, 2026-09-22: "every time I pull bloomberg now, the options are repriced with the
    latest data, and ... the latest data is also logged / overwriting previous marks". Two
    live runs on one day with the spot changed between them: same keys, new values, newer
    stamp, no second row."""
    from engine.options import store

    conn = _new_db()
    _seed_day(conn)
    _seed_option_trade(conn)
    first_pull = datetime.datetime(2026, 8, 21, 10, 27, 44, tzinfo=NY)
    monkeypatch.setattr(store, "_now_ny", lambda: first_pull)
    store.price_all_and_store(conn, DAY)
    before = {r[0]: (r[1], r[2]) for r in _rows(conn, INSTRUMENT, DAY)}
    assert set(before) == SEVEN_TYPES
    assert before["PREMIUM"][1] == "2026-08-21T10:27:44-04:00"

    _set_spot(conn, DAY, SPOT * 1.01)                         # the market moved
    monkeypatch.setattr(store, "_now_ny", lambda: first_pull + datetime.timedelta(hours=12, minutes=41))
    store.price_all_and_store(conn, DAY)
    after = {r[0]: (r[1], r[2]) for r in _rows(conn, INSTRUMENT, DAY)}

    assert set(after) == SEVEN_TYPES                          # same seven keys, no duplicates
    assert conn.execute("SELECT COUNT(*) FROM marks WHERE instrument_id = ? AND as_of_date = ? "
                        "AND source = 'QL_OPTIONS_PRICER'", (INSTRUMENT, DAY)).fetchone()[0] == 7
    for mark_type in ("PREMIUM", "DELTA"):
        assert after[mark_type][0] != before[mark_type][0]     # re-priced with the latest data
        assert after[mark_type][1] == "2026-08-21T23:08:44-04:00" > before[mark_type][1]
    assert after["PREMIUM"][0] > before["PREMIUM"][0]          # a call, spot up
    assert _official(conn, INSTRUMENT, DAY, "PREMIUM") == after["PREMIUM"][0]


@needs_quantlib
def test_the_catch_up_stamps_the_expiry_dates_close_and_the_live_expiry_day_the_pricing_time(monkeypatch):
    from engine.options import store

    # Catch-up: the app did not run on expiry day; a later live run writes the payoff DATED the
    # expiry date, a reconstruction of that day's close, so it carries that day's close stamp.
    conn = _new_db()
    _seed_pair_spot(conn, as_of=EXPIRY, spot=SPOT)
    _seed_option_trade(conn)
    monkeypatch.setattr(store, "_now_ny", lambda: datetime.datetime(2026, 9, 25, 9, 0, 0, tzinfo=NY))
    outcomes = store.price_all_and_store(conn, "2026-09-25")
    assert [(o.priced, o.mark_basis, o.mark_date) for o in outcomes] == [(True, "INTRINSIC", EXPIRY)]
    assert {r[2] for r in _rows(conn, INSTRUMENT, EXPIRY)} == {f"{EXPIRY}T15:00:00-04:00"}

    # Live on the expiry day itself: the payoff at the day's spot, stamped when it was priced.
    conn2 = _new_db()
    _seed_pair_spot(conn2, as_of=EXPIRY, spot=SPOT)
    _seed_option_trade(conn2)
    monkeypatch.setattr(store, "_now_ny", lambda: datetime.datetime(2026, 9, 23, 22, 40, 5, tzinfo=NY))
    outcome = store.price_and_store(conn2, EXPIRY, "T1")
    assert outcome.priced and outcome.mark_basis == "INTRINSIC"
    assert {r[2] for r in _rows(conn2, INSTRUMENT, EXPIRY)} == {"2026-09-23T22:40:05-04:00"}


# --------------------------------------------------------------------------- set_option_terms: a re-save is not a change

@needs_quantlib
@pytest.mark.parametrize("resave", [
    (str(STRIKE), "call", "", None),                 # the grid: strike text, type word, blank payoff, no barrier
    (f"{STRIKE:.6f}", "CALL", "vanilla", 0),          # the editor: formatted strike, lower-case payoff, int barrier
    (STRIKE, " Call ", None, 0.0),                    # padding, None payoff
    (float(f"{STRIKE:.10g}"), "CALL", "VANILLA", "0"),   # a round trip through repr, barrier as text
], ids=["grid", "editor", "padding", "repr"])
def test_a_resave_in_every_shape_the_ui_passes_is_not_a_terms_change(resave):
    """`set_option_terms` deletes every date's pricer marks on a terms CHANGE (2026-09-18).
    It must not take the UI's re-save of the same terms -- strike as text or float,
    barrier None / 0 / '0', type and payoff in any case, '' or None for VANILLA -- for
    one, or the history the backfill rebuilds would be wiped by a click."""
    from engine.options.store import set_option_terms

    conn = _new_db()
    instrument_id = _price_on_two_dates(conn)
    before = _pricer_marks(conn, instrument_id)
    assert len(before) == 14

    set_option_terms(conn, instrument_id, *resave)

    assert _pricer_marks(conn, instrument_id) == before
    assert conn.execute("SELECT strike, option_type, payoff, barrier_level FROM instrument_options "
                        "WHERE instrument_id = ?", (instrument_id,)).fetchone() == (STRIKE, "CALL", "VANILLA", 0.0)


@needs_quantlib
def test_a_resave_of_a_barrier_option_with_the_level_as_text_or_int_is_not_a_change():
    from engine.options.store import set_option_terms

    conn = _new_db()
    instrument_id = _price_on_two_dates(conn, payoff="BARRIER_KO", barrier_level=12.0)
    before = _pricer_marks(conn, instrument_id)
    assert len(before) == 14

    set_option_terms(conn, instrument_id, STRIKE, "CALL", "barrier_ko", "12")
    set_option_terms(conn, instrument_id, str(STRIKE), "call", "BARRIER_KO", 12)

    assert _pricer_marks(conn, instrument_id) == before


# --------------------------------------------------------------------------- closed-out options are not priced (2026-09-22)
# User: "we dont need to price all options, as some of them might be closed out already. If
# they are exactly the same, same strike / underlyer / expiry / type and closed out, we just
# present the buy and sell price as pnl. We dont need to price them individually." The P&L's
# own grouping (engine/pnl/valuation.py::closed_out_from_rows) decides; the pricer imports it.

SELL_BACK_DAY = "2026-08-22"       # the sell-back's trade date: after DAY (08-21), before LATER (08-24)


def _seed_leg(conn, trade_id, expiry=EXPIRY, quantity=NOTIONAL):
    """The NOTIONAL leg the blotter writes for an FX option (the P&L's grouping reads leg 1's
    settle_date as the expiry); _seed_option_trade writes no legs."""
    conn.execute("INSERT OR REPLACE INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                 (trade_id, 1, "NOTIONAL", "EUR", quantity, "2026-08-18", expiry, 0.0, 0))
    conn.commit()


def _seed_bought_and_sold_back(conn, sell_quantity=-NOTIONAL, sell_instrument="EURUSD092326C-197838147"):
    """T1 bought 2026-08-18 and T2 the sell-back dealt SELL_BACK_DAY under another instrument id
    (the export books each fill under its own id), same terms. sell_quantity = -NOTIONAL closes
    the position; a smaller one is a part sell-back."""
    _seed_option_trade(conn, trade_id="T1")
    _seed_leg(conn, "T1")
    _seed_option_trade(conn, trade_id="T2", instrument_id=sell_instrument, quantity=sell_quantity, price=0.0071)
    _seed_leg(conn, "T2", quantity=sell_quantity)
    conn.execute("UPDATE trades SET trade_date = ? WHERE trade_id = 'T2'", (SELL_BACK_DAY,))
    conn.commit()


@needs_quantlib
def test_a_bought_and_sold_back_option_is_not_priced_after_the_close_out_and_is_before_it():
    from engine.options import store

    conn = _new_db()
    _seed_bought_and_sold_back(conn)
    _seed_day(conn, DAY)
    _seed_day(conn, LATER)
    # Before the sell-back (DAY < SELL_BACK_DAY): T1 is live and priced; T2 not yet dealt.
    before = store.price_close(conn, DAY)
    assert before["priced"] == 1 and before["closed_out"] == [] and before["skipped"] == []
    assert len(_rows(conn, INSTRUMENT, DAY)) == 7
    # After it: neither trade is priced, both listed under closed_out, nothing under skipped.
    after = store.price_close(conn, LATER)
    assert after["priced"] == 0 and after["skipped"] == []
    assert after["closed_out"] == ["T1", "T2"]
    assert _rows(conn, INSTRUMENT, LATER) == []
    assert _rows(conn, "EURUSD092326C-197838147", LATER) == []
    # The live pass: the same two, flagged on the outcome with a reason naming the group.
    outcomes = store.price_all_and_store(conn, LATER)
    assert [o.trade_id for o in outcomes] == ["T1", "T2"]
    assert all(o.closed_out and not o.priced for o in outcomes)
    assert outcomes[0].reason.startswith(f"closed out {SELL_BACK_DAY}: bought and sold back with T2")
    assert _rows(conn, INSTRUMENT, LATER) == []
    # The day before the close-out: nothing is closed out yet, so the live pass prices both
    # (it prices every trade on file whatever its trade date: a trade dated tomorrow still
    # needs today's mark for tomorrow's book, price_all_and_store's docstring).
    live_before = store.price_all_and_store(conn, DAY)
    assert {o.trade_id: (o.priced, o.closed_out) for o in live_before} == {"T1": (True, False), "T2": (True, False)}


@needs_quantlib
def test_a_part_sell_back_is_still_priced():
    from engine.options import store

    conn = _new_db()
    _seed_bought_and_sold_back(conn, sell_quantity=-NOTIONAL / 2)
    _seed_day(conn, LATER)
    out = store.price_close(conn, LATER)
    assert out["closed_out"] == [] and out["skipped"] == [] and out["priced"] == 2
    assert all(not o.closed_out and o.priced for o in store.price_all_and_store(conn, LATER))


@needs_quantlib
def test_recalc_on_file_reports_the_closed_out_count_apart_from_skipped(monkeypatch):
    from engine.options import store

    monkeypatch.setattr(store, "purge_old_unit_cash_payoff_marks", lambda conn: {"ran": False, "marks_deleted": 0})
    conn = _new_db()
    _seed_bought_and_sold_back(conn)
    _seed_day(conn, DAY)
    _seed_day(conn, LATER)
    out = store.recalc_on_file(conn, SECOND_AS_OF)
    assert "error" not in out
    by_day = {d["day"]: d for d in out["days"]}
    assert by_day[DAY]["priced"] == 1 and by_day[DAY]["closed_out"] == []
    assert by_day[LATER]["priced"] == 0 and by_day[LATER]["closed_out"] == ["T1", "T2"] and by_day[LATER]["skipped"] == []
    assert out["priced"] == 1 and out["skipped"] == 0 and out["closed_out"] == 2


def test_the_expiry_day_catch_up_leaves_a_closed_out_option_alone():
    """Expired before as_of with no expiry-dated mark: the catch-up would write the payoff;
    closed out, it writes nothing (the ledger's CLOSE_OUT freeze covers the group). No
    QuantLib needed: nothing is priced."""
    from engine.options import store

    conn = _new_db()
    _seed_bought_and_sold_back(conn)
    after_expiry = "2026-09-24"
    _seed_pair_spot(conn, as_of=EXPIRY, spot=SPOT)   # the expiry date's own SPOT, so a catch-up could run
    outcomes = store.price_all_and_store(conn, after_expiry)
    assert all(o.closed_out and not o.priced for o in outcomes)
    assert conn.execute("SELECT COUNT(*) FROM marks WHERE source = 'QL_OPTIONS_PRICER'").fetchone()[0] == 0


# --------------------------------------------------------------------------- options on commodity futures (2026-09-24)
# Commodity conversion Phase 5: the bulk passes write the Greeks of every CMDTY_OPTION open on
# the date priced (engine.options.equity_commodity.price_listed_commodity_option, the vol that
# Bloomberg's own price implies), each instrument once, closed-out groups skipped and reported,
# no expiry-day payoff. The December 2026 WTI 80 call on the Dec future.

CL_ROOT = "NYMEX:CL"
CL_FUTURE = "CLZ26 Comdty"
CL_FUTURE_EXPIRY = "2026-11-19"
CL_OPTION = "CLZ26C 80 Comdty"
CL_OPTION_EXPIRY = "2026-11-16"
CL_MULT = 1000.0
CL_DEALT = "2026-08-18"
_INSTRUMENT_COLS = "(instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, bbg_ticker, expiry_date)"


def _seed_cl_option(conn, strike=80.0, payoff="VANILLA"):
    conn.execute(f"INSERT OR IGNORE INTO instruments {_INSTRUMENT_COLS} VALUES (?,?,?,?,?,?,?,?)",
                 (CL_FUTURE, "FUTURE", CL_ROOT, "USD", CL_MULT, 0, "CLZ6 Comdty", CL_FUTURE_EXPIRY))
    conn.execute(f"INSERT OR IGNORE INTO instruments {_INSTRUMENT_COLS} VALUES (?,?,?,?,?,?,?,?)",
                 (CL_OPTION, "CMDTY_OPTION", CL_ROOT, "USD", CL_MULT, 0, "", CL_OPTION_EXPIRY))
    conn.execute("INSERT OR REPLACE INTO instrument_options (instrument_id, strike, option_type, payoff) "
                 "VALUES (?,?,?,?)", (CL_OPTION, strike, "CALL", payoff))
    conn.commit()


def _seed_cl_trade(conn, trade_id, quantity, fill=2.10, trade_date=CL_DEALT):
    _seed_cl_option(conn)
    conn.execute("INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, "
                 "price, account, counterparty, strategy, trader, description, theme) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (trade_id, "XLSX", CL_OPTION, "CMDTY_OPTION", trade_id, trade_date, quantity, fill,
                  "acc", "cp", "", "t", "d", ""))
    conn.execute("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                 (trade_id, 1, "NOTIONAL", "USD", quantity * CL_MULT * fill, trade_date, CL_OPTION_EXPIRY, fill, 0))
    conn.commit()


def _seed_cl_px(conn, day, instrument_id, value):
    settle = CL_OPTION_EXPIRY if instrument_id == CL_OPTION else CL_FUTURE_EXPIRY
    conn.execute("INSERT OR REPLACE INTO marks (as_of_date, instrument_id, settle_date, mark_type, value, source, "
                 "snapped_at) VALUES (?,?,?,?,?,?,?)",
                 (day, instrument_id, settle, "FUTURE_PX", value, "BBG_BDH", f"{day}T17:00:00-04:00"))
    conn.commit()


def _seed_cl_day(conn, day, future=78.0, option=2.50, curve=True, underlying=True):
    """Bloomberg's price of the option and (unless `underlying` is False) of the Dec future on
    `day`, and a flat USD SOFR curve for that day."""
    from tests.test_options_pricing import _seed_ois_curve

    _seed_cl_px(conn, day, CL_OPTION, option)
    if underlying:
        _seed_cl_px(conn, day, CL_FUTURE, future)
    if curve:
        _seed_ois_curve(conn, day, "USD")
        conn.commit()


def _cl_marks(conn, day):
    return {mt: (v, settle, stamp) for mt, v, settle, stamp in conn.execute(
        "SELECT mark_type, value, settle_date, snapped_at FROM marks WHERE instrument_id = ? AND as_of_date = ? "
        "AND source = 'QL_OPTIONS_PRICER'", (CL_OPTION, day))}


@needs_quantlib
def test_the_live_pass_writes_an_option_on_a_futures_greeks_at_its_expiry_key(monkeypatch):
    from engine.options import store

    conn = _new_db()
    _seed_cl_trade(conn, "C1", 10)
    _seed_cl_day(conn, DAY)
    monkeypatch.setattr(store, "_now_ny", lambda: datetime.datetime(2026, 8, 21, 22, 31, 12, tzinfo=NY))

    outcomes = store.price_all_and_store(conn, DAY)

    assert [(o.trade_id, o.product, o.priced, o.closed_out) for o in outcomes] == [("C1", "CMDTY_OPTION", True, False)]
    o = outcomes[0]
    assert o.reason == "" and o.implied_vol and 0.05 < o.implied_vol < 1.0
    assert o.vol_source_kind == "IMPLIED" and o.mark_basis == "MODEL" and o.mark_date == DAY
    marks = _cl_marks(conn, DAY)
    assert set(marks) == {"PREMIUM", "DELTA", "GAMMA", "THETA", "VEGA", "RHO"}
    assert {settle for _v, settle, _s in marks.values()} == {CL_OPTION_EXPIRY}
    assert {stamp for _v, _settle, stamp in marks.values()} == {"2026-08-21T22:31:12-04:00"}   # the pricing time
    assert marks["PREMIUM"][0] == pytest.approx(2.50)            # Bloomberg's own price, as quoted
    assert 0.0 < marks["DELTA"][0] < 1.0                         # futures lots per option lot, an OTM call
    assert _official(conn, CL_OPTION, DAY, "DELTA") == marks["DELTA"][0]   # official under QL_OPTIONS_PRICER


@needs_quantlib
def test_one_option_with_two_trades_is_priced_once_and_each_trade_reported(monkeypatch):
    from engine.options import equity_commodity, store

    calls = []
    real = equity_commodity.price_listed_commodity_option

    def counting(conn, inst, as_of, curve_cache=None):
        calls.append((inst, as_of))
        return real(conn, inst, as_of, curve_cache)

    monkeypatch.setattr(equity_commodity, "price_listed_commodity_option", counting)
    conn = _new_db()
    _seed_cl_trade(conn, "C1", 10)
    _seed_cl_trade(conn, "C2", 5, fill=2.30)
    _seed_cl_day(conn, DAY)

    outcomes = store.price_all_and_store(conn, DAY)
    assert calls == [(CL_OPTION, DAY)]
    assert [(o.trade_id, o.priced) for o in outcomes] == [("C1", True), ("C2", True)]

    calls.clear()
    out = store.price_close(conn, DAY)
    assert calls == [(CL_OPTION, DAY)]
    assert out == {"day": DAY, "priced": 2, "skipped": [], "closed_out": [], "futures_options_priced": 2}


@needs_quantlib
def test_an_option_on_a_future_bought_and_sold_back_on_one_id_is_closed_out_and_not_priced():
    """The buy and the sell-back sit on ONE canonical id here (unlike the FX export's two ids);
    the P&L's grouping is on the terms, so they group all the same."""
    from engine.options import store

    conn = _new_db()
    _seed_cl_trade(conn, "C1", 10)
    _seed_cl_trade(conn, "C2", -10, fill=2.60, trade_date=SELL_BACK_DAY)
    _seed_cl_day(conn, DAY)
    _seed_cl_day(conn, LATER, future=79.0, option=2.90)

    before = store.price_close(conn, DAY)                        # C2 not dealt yet: C1 is live
    assert before == {"day": DAY, "priced": 1, "skipped": [], "closed_out": [], "futures_options_priced": 1}

    after = store.price_close(conn, LATER)
    assert after == {"day": LATER, "priced": 0, "skipped": [], "closed_out": ["C1", "C2"],
                     "futures_options_priced": 0}
    assert _cl_marks(conn, LATER) == {}

    outcomes = store.price_all_and_store(conn, LATER)
    assert [(o.trade_id, o.closed_out, o.priced, o.product) for o in outcomes] == [
        ("C1", True, False, "CMDTY_OPTION"), ("C2", True, False, "CMDTY_OPTION")]
    assert outcomes[0].reason.startswith(f"closed out {SELL_BACK_DAY}: bought and sold back with C2")
    assert _cl_marks(conn, LATER) == {}


def test_closed_out_listed_options_leaves_a_part_sell_back_and_an_unknown_strike_live():
    from engine.options import store

    conn = _new_db()
    _seed_cl_trade(conn, "C1", 10)
    _seed_cl_trade(conn, "C2", -4, trade_date=SELL_BACK_DAY)
    assert store.closed_out_listed_options(conn, LATER) == {}
    _seed_cl_trade(conn, "C3", -6, trade_date=SELL_BACK_DAY)
    assert set(store.closed_out_listed_options(conn, LATER)) == {"C1", "C2", "C3"}
    conn.execute("UPDATE instrument_options SET strike = 0 WHERE instrument_id = ?", (CL_OPTION,))
    assert store.closed_out_listed_options(conn, LATER) == {}    # strike not known: never matched


@needs_quantlib
def test_an_option_on_a_future_with_no_underlying_price_is_reported_with_its_reason():
    from engine.options import store

    conn = _new_db()
    _seed_cl_trade(conn, "C1", 10)
    _seed_cl_day(conn, DAY, underlying=False)

    out = store.price_close(conn, DAY)
    assert out["priced"] == 0 and out["closed_out"] == [] and out["futures_options_priced"] == 0
    assert out["skipped"] == [{"trade_id": "C1", "reason": f"{DAY} close: no Bloomberg price of the underlying "
                                                           f"future {CL_FUTURE} on {DAY}"}]
    outcomes = store.price_all_and_store(conn, DAY)
    assert [(o.priced, o.closed_out, o.product) for o in outcomes] == [(False, False, "CMDTY_OPTION")]
    assert outcomes[0].reason == f"no Bloomberg price of the underlying future {CL_FUTURE} on {DAY}"
    assert _cl_marks(conn, DAY) == {}


@needs_quantlib
def test_price_close_prices_an_option_on_a_future_from_that_days_marks_only():
    from engine.options import store

    conn = _new_db()
    _seed_cl_trade(conn, "C1", 10)
    _seed_cl_day(conn, DAY, future=78.0, option=2.50)
    _seed_cl_day(conn, LATER, future=82.0, option=4.40)

    out = store.price_close(conn, DAY)
    assert out["priced"] == 1 and out["futures_options_priced"] == 1
    marks = _cl_marks(conn, DAY)
    assert marks["PREMIUM"][0] == pytest.approx(2.50)
    assert {stamp for _v, _settle, stamp in marks.values()} == {f"{DAY}T15:00:00-04:00"}   # the day's close
    assert _cl_marks(conn, LATER) == {}                          # nothing written for another day
    store.price_close(conn, LATER)
    assert _cl_marks(conn, LATER)["DELTA"][0] > marks["DELTA"][0]   # the future up: the call's delta up

    # DAY's underlying price gone: DAY is skipped with DAY's reason, never priced off LATER's.
    conn.execute("DELETE FROM marks WHERE as_of_date = ? AND instrument_id = ?", (DAY, CL_FUTURE))
    conn.execute("DELETE FROM marks WHERE as_of_date = ? AND source = 'QL_OPTIONS_PRICER'", (DAY,))
    conn.commit()
    out = store.price_close(conn, DAY)
    assert out["priced"] == 0 and out["skipped"][0]["reason"].startswith(f"{DAY} close: no Bloomberg price")
    assert _cl_marks(conn, DAY) == {}


@needs_quantlib
def test_no_expiry_day_payoff_for_an_option_on_a_future_and_an_expired_one_is_not_listed():
    from engine.options import store

    conn = _new_db()
    _seed_cl_trade(conn, "C1", 10)
    _seed_cl_day(conn, CL_OPTION_EXPIRY)
    out = store.price_close(conn, CL_OPTION_EXPIRY)              # open on its expiry date: the pricer's reason
    assert out["priced"] == 0
    assert out["skipped"] == [{"trade_id": "C1", "reason": f"{CL_OPTION_EXPIRY} close: expiry {CL_OPTION_EXPIRY} "
                                                           f"is not after as_of {CL_OPTION_EXPIRY}"}]
    assert _cl_marks(conn, CL_OPTION_EXPIRY) == {}               # no payoff written: its P&L is Bloomberg's price
    assert store.price_all_and_store(conn, "2026-11-17") == []   # expired: not in the book
    assert store.price_close(conn, "2026-11-17")["skipped"] == []


@needs_quantlib
def test_recalc_on_file_visits_the_days_with_the_options_and_the_underlyings_prices(monkeypatch):
    from engine.options import store

    conn = _new_db()
    _seed_cl_trade(conn, "C1", 10)
    _seed_cl_day(conn, DAY)
    _seed_cl_day(conn, SELL_BACK_DAY, underlying=False)         # the option's price alone: not a day to visit
    _seed_cl_day(conn, LATER, future=80.0, option=3.20)
    as_of = "2026-08-25"
    monkeypatch.setattr(store, "_now_ny", lambda: datetime.datetime(2026, 8, 25, 9, 30, 0, tzinfo=NY))

    out = store.recalc_on_file(conn, as_of)
    assert "error" not in out and out["since"] == CL_DEALT
    assert [d["day"] for d in out["days"]] == [DAY, LATER, as_of]
    assert [d["futures_options_priced"] for d in out["days"]] == [1, 1, 0]
    assert out["priced"] == 2 and out["futures_options_priced"] == 2 and out["skipped"] == 1   # as_of: no price yet
    assert {s for _v, _settle, s in _cl_marks(conn, LATER).values()} == {f"{LATER}T15:00:00-04:00"}
    assert _cl_marks(conn, SELL_BACK_DAY) == {}


@needs_quantlib
def test_the_fx_options_are_unchanged_by_an_option_on_a_future_beside_them(monkeypatch):
    from engine.options import store

    monkeypatch.setattr(store, "_now_ny", lambda: datetime.datetime(2026, 8, 21, 22, 31, 12, tzinfo=NY))

    def fx_run(with_listed):
        conn = _new_db()
        _seed_day(conn)                                          # EURUSD SPOT, USD + EUR curves, vol
        _seed_option_trade(conn)
        if with_listed:
            _seed_cl_trade(conn, "C1", 10)
            _seed_cl_day(conn, DAY, curve=False)                 # the USD curve is the FX seed's own
        live = store.price_all_and_store(conn, DAY)
        close = store.price_close(conn, DAY)
        return conn, live, close

    alone_conn, alone_live, alone_close = fx_run(False)
    both_conn, both_live, both_close = fx_run(True)
    assert [(o.trade_id, o.product, o.priced) for o in alone_live] == [("T1", "FX_OPTION", True)]
    assert [(o.trade_id, o.product, o.priced) for o in both_live] == [("T1", "FX_OPTION", True),
                                                                       ("C1", "CMDTY_OPTION", True)]
    assert both_live[0].result == alone_live[0].result
    assert _rows(both_conn, INSTRUMENT, DAY) == _rows(alone_conn, INSTRUMENT, DAY)
    assert alone_close == {"day": DAY, "priced": 1, "skipped": [], "closed_out": [], "futures_options_priced": 0}
    assert both_close == {"day": DAY, "priced": 2, "skipped": [], "closed_out": [], "futures_options_priced": 1}
