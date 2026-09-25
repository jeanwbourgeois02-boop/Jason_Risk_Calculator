"""spreads-engine, Phase C: the daily history of one spread position (LTD USD and level per date),
for the Spreads tab's drill-down chart. Nothing is a new figure: the LTD is the members'
value_book rows summed, the level is level_now's rule read from that date's rows."""
import json

import pytest

from engine.pnl.valuation import value_book
from engine.spreads import CALENDAR, book_spreads, history_dates, position_history
from engine.spreads.levels import spec_from_dict
from tests.test_spread_levels import AS_OF, CLF7, CLZ6, PREV, TD, TD2, _db, _future, _only, _px, _two_entries

PERIOD_KEYS = ("daily", "d5", "mtd", "ytd")


def _memo(fn=value_book):
    cache = {}

    def read(conn, day):
        if day not in cache:
            cache[day] = fn(conn, day)
        return cache[day]
    read.calls = cache
    return read


def _points(hist):
    return {p["date"]: p for p in hist["points"]}


# ------------------------------------------------------------------ the sample book
def test_the_sample_book_history_agrees_with_the_positions():
    from tests.golden_book import AS_OF_DATES, build_book, mark_dates

    conn = _db()
    build_book(conn)
    read = _memo()
    compared = levels = 0
    for as_of in AS_OF_DATES:
        out = book_spreads(conn, as_of, value_fn=read)
        assert out["positions"]
        for pos in out["positions"]:
            refs = [pos["ref_dates"][p] for p in PERIOD_KEYS]
            dates = sorted({d for d in mark_dates() if d <= as_of} | set(refs) | {as_of})
            hist = position_history(conn, pos, dates, value_fn=read)
            pts = _points(hist)
            last = hist["points"][-1]
            assert last["date"] == as_of
            # the last point is the position's own LTD and level now
            if pos["pnl_usd"]["ltd"] is None:
                assert last["ltd_usd"] is None and last["ltd_reasons"]
            else:
                assert last["ltd_usd"] == pytest.approx(pos["pnl_usd"]["ltd"], rel=1e-12, abs=1e-6)
                assert last["ltd_excluded"] == pos["pnl_excluded"]["ltd"]
            if pos["level_now"] is None:
                assert last["level"] is None and last["level_reason"]
            else:
                assert last["level"] == pytest.approx(pos["level_now"], rel=1e-12, abs=1e-12)
                assert last["level_reason"] == "" and hist["level_unit"] == pos["level_unit"]
                levels += 1
            # LTD(a) - LTD(ref) over the history is the period P&L the position shows
            for p in PERIOD_KEYS:
                if pos["pnl_usd"][p] is None or pos["pnl_excluded"][p]:
                    continue
                ref = pos["ref_dates"][p]
                if ref < hist["first_trade_date"]:
                    assert ref not in pts and ref in hist["dates_left_out"]
                    ref_ltd = 0.0                       # not traded yet: nothing to measure from
                else:
                    assert pts[ref]["ltd_excluded"] == 0
                    ref_ltd = pts[ref]["ltd_usd"]
                assert last["ltd_usd"] - ref_ltd == pytest.approx(pos["pnl_usd"][p], rel=1e-9, abs=1e-6)
                compared += 1
            # n/a is never 0
            for pt in hist["points"]:
                if pt["ltd_usd"] is None:
                    assert pt["ltd_reasons"]
                if pt["level"] is None:
                    assert pt["level_reason"]
    assert compared >= 20 and levels >= 3                 # the check bit on real figures


def test_the_sample_calendar_over_its_business_days():
    from tests.golden_book import build_book

    conn = _db()
    build_book(conn)
    as_of = "2026-09-18"
    read = _memo()
    pos = _only(book_spreads(conn, as_of, value_fn=read)["positions"], kind=CALENDAR)
    dates = history_dates(conn, pos, as_of)
    assert dates[0] == hist_first(conn, pos) and dates[-1] == as_of
    assert all(d[:4] == "2026" for d in dates)
    hist = position_history(conn, pos, dates, value_fn=read)
    assert [p["date"] for p in hist["points"]] == dates and hist["dates_left_out"] == []
    assert len(read.calls) >= len(dates)                  # one valuation per date, as the docstring says
    assert hist["points"][-1]["ltd_usd"] == pytest.approx(pos["pnl_usd"]["ltd"])
    # the two entries: only the first is on before the second's trade date
    first_on = [p["members_on"] for p in hist["points"]]
    assert first_on[0] == 1 and first_on[-1] == 2


def hist_first(conn, pos):
    return min(r[0] for r in conn.execute(
        f"SELECT trade_date FROM trades WHERE trade_id IN ({','.join('?' * len(pos['trade_ids']))})",
        pos["trade_ids"]))


# ------------------------------------------------------------------ hand-built books
def _two_entry_book():
    conn = _db()
    _two_entries(conn)                                   # A on TD (2 lots), B on TD2 (3 lots); marks PREV, AS_OF
    for day, (z, f) in ((TD, (70.1, 69.6)), (TD2, (70.4, 69.9))):
        _px(conn, CLZ6, z, day)
        _px(conn, CLF7, f, day)
    return conn


def test_only_trades_already_traded_count_and_earlier_dates_are_left_out():
    conn = _two_entry_book()
    pos = _only(book_spreads(conn, AS_OF)["positions"])
    hist = position_history(conn, pos, ["2026-08-31", AS_OF, TD, TD2, TD])
    assert hist["first_trade_date"] == TD and hist["dates_left_out"] == ["2026-08-31"]
    pts = _points(hist)
    assert list(pts) == [TD, TD2, AS_OF]
    assert pts[TD]["members_on"] == 1 and pts[TD2]["members_on"] == 2
    a_on_td = sum(r["pnl_usd"] for r in value_book(conn, TD).to_dict("records") if r["trade_id"] in ("A1", "A2"))
    assert pts[TD]["ltd_usd"] == pytest.approx(a_on_td)
    assert pts[TD]["ltd_usd"] == pytest.approx(2 * 1000 * ((70.1 - 70.0) - (69.6 - 69.5)))
    assert pts[TD]["level"] == pytest.approx(70.1 - 69.6)
    assert pts[AS_OF]["ltd_usd"] == pytest.approx(pos["pnl_usd"]["ltd"])
    assert pts[AS_OF]["level"] == pytest.approx(pos["level_now"]) and pts[AS_OF]["level_source"]
    daily = position_history(conn, pos, [PREV, AS_OF])["points"]
    assert daily[1]["ltd_usd"] - daily[0]["ltd_usd"] == pytest.approx(pos["pnl_usd"]["daily"])
    assert daily[1]["level"] - daily[0]["level"] == pytest.approx(pos["level_change"])


def test_a_leg_with_no_price_is_na_with_its_reason_never_zero():
    conn = _db()
    _future(conn, "W1", CLZ6, 2, 70.0)
    _future(conn, "W2", CLF7, -2, 69.5)
    _px(conn, CLZ6, 71.0)                               # CLF27 has no price on any date
    pos = _only(book_spreads(conn, AS_OF)["positions"])
    pt = position_history(conn, pos, [AS_OF])["points"][0]
    assert pt["ltd_usd"] is None and pt["ltd_excluded"] == 1
    assert "W2" in pt["ltd_reasons"] and pt["ltd_reasons"].startswith(pos["spread_ids"][0] + ": ")
    assert pt["level"] is None and CLF7 in pt["level_reason"]


def test_a_member_unpriced_on_a_date_is_counted_not_summed():
    conn = _two_entry_book()
    pos = _only(book_spreads(conn, AS_OF)["positions"])

    def blank_b(conn_, day):
        df = value_book(conn_, day)
        if day == AS_OF:
            hit = df["trade_id"] == "B2"
            df.loc[hit, "pnl_usd"] = float("nan")
            df.loc[hit, "reason"] = "no FUTURE_PX (test)"
        return df

    pt = position_history(conn, pos, [AS_OF], value_fn=blank_b)["points"][0]
    a_only = sum(r["pnl_usd"] for r in value_book(conn, AS_OF).to_dict("records") if r["trade_id"] in ("A1", "A2"))
    assert pt["ltd_usd"] == pytest.approx(a_only) and pt["ltd_excluded"] == 1 and pt["members_on"] == 2
    assert pt["ltd_reasons"] == "SPREAD-B1: unpriced on 2026-09-15: B2 (no FUTURE_PX (test))"
    assert pt["level"] is not None                     # CLF27 still has a priced row (A2)


def test_the_filled_reader_tuple_is_accepted_and_its_fills_are_counted():
    conn = _two_entry_book()
    pos = _only(book_spreads(conn, AS_OF)["positions"])

    def filled(conn_, day):
        df = value_book(conn_, day)
        df.loc[df["trade_id"] == "A1", "note"] = f"no price on {day}: value of the {PREV} close"
        return df, 1, len(df)

    pt = position_history(conn, pos, [AS_OF], value_fn=filled)["points"][0]
    assert pt["ltd_usd"] == pytest.approx(pos["pnl_usd"]["ltd"])
    assert pt["ltd_filled"] == 1 and pt["ltd_notes"].startswith("A1: no price on 2026-09-15")


def test_a_date_that_cannot_be_valued_blanks_that_point_only():
    conn = _two_entry_book()
    pos = _only(book_spreads(conn, AS_OF)["positions"])

    def broken(conn_, day):
        if day == PREV:
            raise ValueError("boom")
        return value_book(conn_, day)

    pts = _points(position_history(conn, pos, [PREV, AS_OF], value_fn=broken))
    assert pts[PREV]["ltd_usd"] is None and "could not be valued" in pts[PREV]["ltd_reasons"]
    assert pts[PREV]["level"] is None and pts[PREV]["level_reason"]
    assert pts[AS_OF]["ltd_usd"] == pytest.approx(pos["pnl_usd"]["ltd"])


def test_bare_trade_ids_give_the_ltd_but_no_level():
    conn = _two_entry_book()
    hist = position_history(conn, ["A1", "A2"], [TD2, AS_OF])
    assert hist["position_id"] == "" and hist["level_unit"] == ""
    pts = _points(hist)
    assert pts[AS_OF]["ltd_usd"] == pytest.approx(
        sum(r["pnl_usd"] for r in value_book(conn, AS_OF).to_dict("records") if r["trade_id"] in ("A1", "A2")))
    assert pts[AS_OF]["level"] is None and "only trade ids" in pts[AS_OF]["level_reason"]


def test_a_position_is_json_safe_and_its_level_spec_round_trips():
    conn = _two_entry_book()
    pos = _only(book_spreads(conn, AS_OF)["positions"])
    assert pos["member_trade_ids"] == {"SPREAD-A1": ["A1", "A2"], "SPREAD-B1": ["B1", "B2"]}
    again = json.loads(json.dumps(pos))                  # a screen may keep it in a dcc.Store
    spec = spec_from_dict(again["level_spec"])
    assert spec.unit == "USD/bbl" and [leg.trade_ids for leg in spec.legs] == [("A1", "B1"), ("A2", "B2")]
    assert position_history(conn, again, [AS_OF])["points"][0]["level"] == pytest.approx(pos["level_now"])
