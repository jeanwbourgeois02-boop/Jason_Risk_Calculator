"""Exposure adapter (engine/ladder/exposure_adapter.py): DB-recorded trades -> exposure
records. `records_from_parse` and its BNP-CSV-fixture tests were removed 2026-09-17
along with `data/ingest/bnp.py` (the retired BNP parser, "no bnp fall back" --
docs/bnp-excel-removal.md): the adapter's only live path is `records_from_db` /
`exposure_records_from_db`, exercised below directly against the schema."""
import pytest

from engine.ladder.exposure import build_exposure


def test_aud_screenshot_result_preserved():
    from tests.test_exposure import AUD_FIXTURE, rate
    s = build_exposure(AUD_FIXTURE, {"AUD": rate(0.600)}).summary.iloc[0]
    assert round(s["usd_delta"]) == -12576974


def test_records_from_db_fx_forward_two_legs_and_grid_vs_exposure_boundary():
    """Direct DB fixture (no parser involved): one FX forward -> two leg records, with
    the grid's `settle_date >= as_of` vs exposure's `settle_date > as_of` boundary
    (CLAUDE.md "Six tabs as views") both exercised."""
    from data.ingest import schema
    from engine.ladder.exposure_adapter import records_from_db, exposure_records_from_db

    conn = schema.connect()
    conn.execute("INSERT INTO instruments VALUES ('USDJPY','FX','USD','JPY',1,0,'USDJPY Curncy','9999-12-31')")
    conn.execute("INSERT INTO trades VALUES ('t1','XLSX','USDJPY','FX_FWD','t1','2026-08-20',"
                 "1000000,147.0,'acc','cp','HAHY7','t','desc','')")
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("t1", 1, "FX_NEAR", "USD", 1000000.0, "2026-08-20", "2026-09-16", 147.0, 1),
        ("t1", 2, "FX_NEAR", "JPY", -147000000.0, "2026-08-20", "2026-09-16", 147.0, 1),
    ])
    conn.commit()

    records, unresolved = records_from_db(conn, "2026-08-20")
    assert unresolved == []
    by_ccy = {r["currency"]: r for r in records}
    assert set(by_ccy) == {"USD", "JPY"}
    assert by_ccy["USD"]["local_amount"] == pytest.approx(1000000.0)
    assert by_ccy["JPY"]["local_amount"] == pytest.approx(-147000000.0)
    assert by_ccy["USD"]["book"] == "HAHY7" and by_ccy["USD"]["fund"] == "NMMF"

    from engine.ladder.exposure_adapter import SETTLED
    # grid rule (>=): a leg settling exactly on as_of is shown on its own date row (cash
    # moving today), not yet in the settled row.
    grid_today, _ = records_from_db(conn, "2026-09-16")
    assert {r["settlement_date"] for r in grid_today} == {"2026-09-16"}
    # exposure rule (2026-09-18): by close of settlement day a DELIVERABLE leg is cash
    # and still carries delta -- it moves into the settled records instead of dropping.
    exp_today, _ = exposure_records_from_db(conn, "2026-09-16")
    assert {r["settlement_date"] for r in exp_today} == {SETTLED}
    assert {r["currency"]: r["local_amount"] for r in exp_today} == {"USD": 1000000.0, "JPY": -147000000.0}
    # once truly settled both rules agree: one settled record per currency, nothing open.
    grid_after, unresolved_after = records_from_db(conn, "2026-09-17")
    assert unresolved_after == []
    assert {r["settlement_date"] for r in grid_after} == {SETTLED}
    assert all(r["settled_on"] == "2026-09-16" and r["settles_cash"] == 1 for r in grid_after)
    # the open-legs-only view is still available on request.
    assert records_from_db(conn, "2026-09-17", include_settled=False) == ([], [])
    assert exposure_records_from_db(conn, "2026-09-17", include_settled=False) == ([], [])


def _fresh():
    from data.ingest import schema
    return schema.connect()


def _trade(conn, trade_id, pair, product, qty, price, trade_date="2026-08-20", package=None):
    conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (trade_id, "XLSX", pair, product, package or trade_id, trade_date, qty, price,
                  "acc", "cp", "HAHY7", "t", "desc", ""))


def _leg(conn, trade_id, leg_no, leg_type, ccy, amount, settle, rate, settles_cash):
    conn.execute("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                 (trade_id, leg_no, leg_type, ccy, amount, "2026-08-20", settle, rate, settles_cash))


def test_settled_ndf_ticket_is_gone_from_the_ladder_realised_or_not():
    """An NDF never delivers KRW, and once fixed it is gone from the ladder altogether
    (user, 2026-09-22: "NDFs - once they expire, they should disappear, not become setteld
    cash ... 0 delta and 0 carry"): nothing on the grid, nothing in Settled cash -- not its
    legs, not its USD settlement -- and nothing named under the grid, with or without a
    realised_pnl row. Its P&L lives in the Blotter, frozen at the fixing."""
    from engine.ladder.exposure_adapter import SETTLED, records_from_db, exposure_records_from_db
    conn = _fresh()
    conn.execute("INSERT INTO instruments VALUES ('USDKRW','FX','USD','KRW',1,1,'USDKRW Curncy','9999-12-31')")
    _trade(conn, "n1", "USDKRW", "FX_FWD", -1_000_000.0, 1413.138, trade_date="2026-08-17")
    _leg(conn, "n1", 1, "FX_NEAR", "USD", -1_000_000.0, "2026-09-16", 1413.138, 0)
    _leg(conn, "n1", 2, "FX_NEAR", "KRW", 1_413_138_000.0, "2026-09-16", 1413.138, 0)
    conn.commit()

    for day in ("2026-09-15", "2026-09-18"):   # from the fixing (Mon 09-14) on, and after the value date
        for fn in (records_from_db, exposure_records_from_db):
            assert fn(conn, day) == ([], [])

    conn.execute("INSERT INTO realised_pnl (trade_id, instrument_id, product, currency, settle_date, local_amount,"
                 " usd_entry_amount, mark_type, spot_usd_per_local, spot_as_of_date, spot_source, pnl_usd,"
                 " frozen_at, note) VALUES ('n1','USDKRW','FX_FWD','KRW','2026-09-16',13138000.0,0.0,'SPOT',"
                 " 0.000714286,'2026-09-16','BBG_BFXFORWARD',9384.0,'2026-09-17T00:00:00+00:00','')")
    conn.commit()
    for fn in (records_from_db, exposure_records_from_db):
        assert fn(conn, "2026-09-18") == ([], [])
    # before the fixing it is on the grid under its fixing date, as ever
    grid, named = records_from_db(conn, "2026-09-11")
    assert named == [] and {(r["currency"], r["settlement_date"]) for r in grid} == {("USD", "2026-09-14"), ("KRW", "2026-09-14")}
    assert SETTLED not in {r["settlement_date"] for r in grid}


def test_settled_deliverable_ticket_is_its_legs_not_its_realised_pnl():
    """A deliverable forward with a realised_pnl row settles as its two legs only: the
    P&L is already inside those amounts, adding it again would double count."""
    from engine.ladder.exposure_adapter import SETTLED, records_from_db
    conn = _fresh()
    conn.execute("INSERT INTO instruments VALUES ('USDNOK','FX','USD','NOK',1,0,'USDNOK Curncy','9999-12-31')")
    _trade(conn, "d1", "USDNOK", "FX_FWD", -1_000_000.0, 10.0)
    _leg(conn, "d1", 1, "FX_NEAR", "USD", -1_000_000.0, "2026-09-16", 10.0, 1)
    _leg(conn, "d1", 2, "FX_NEAR", "NOK", 10_000_000.0, "2026-09-16", 10.0, 1)
    conn.execute("INSERT INTO realised_pnl (trade_id, instrument_id, product, currency, settle_date, local_amount,"
                 " usd_entry_amount, mark_type, spot_usd_per_local, spot_as_of_date, spot_source, pnl_usd,"
                 " frozen_at, note) VALUES ('d1','USDNOK','FX_FWD','NOK','2026-09-16',-1e6,0.0,'SPOT',"
                 " 0.1,'2026-09-16','BBG_BFXFORWARD',-20000.0,'2026-09-17T00:00:00+00:00','')")
    conn.commit()
    records, unresolved = records_from_db(conn, "2026-09-18")
    assert unresolved == []
    assert sorted((r["currency"], r["local_amount"]) for r in records) == [("NOK", 10_000_000.0), ("USD", -1_000_000.0)]
    assert all(r["settlement_date"] == SETTLED for r in records)


def test_settled_swap_legs_aggregate_to_one_record_per_currency_and_build():
    """Both legs of a settled swap in the same currency collapse into one settled
    record (the exposure engine keys records by (trade, currency, settlement_date)),
    and build_exposure accepts the result with a single 'settled' ladder row on top of
    the open dates, summed into that currency's delta."""
    from engine.ladder.exposure import build_exposure
    from engine.ladder.exposure_adapter import SETTLED, records_from_db
    conn = _fresh()
    conn.execute("INSERT INTO instruments VALUES ('USDJPY','FX','USD','JPY',1,0,'USDJPY Curncy','9999-12-31')")
    _trade(conn, "s1", "USDJPY", "FX_SWAP", 1_000_000.0, 147.0, package="SWAP-s1")
    _leg(conn, "s1", 1, "FX_NEAR", "USD", 1_000_000.0, "2026-09-01", 147.0, 1)
    _leg(conn, "s1", 2, "FX_NEAR", "JPY", -147_000_000.0, "2026-09-01", 147.0, 1)
    _leg(conn, "s1", 3, "FX_FAR", "USD", -1_000_000.0, "2026-09-10", 147.5, 1)
    _leg(conn, "s1", 4, "FX_FAR", "JPY", 147_500_000.0, "2026-09-10", 147.5, 1)
    _trade(conn, "o1", "USDJPY", "FX_FWD", 2_000_000.0, 146.0)
    _leg(conn, "o1", 1, "FX_NEAR", "USD", 2_000_000.0, "2026-09-25", 146.0, 1)
    _leg(conn, "o1", 2, "FX_NEAR", "JPY", -292_000_000.0, "2026-09-25", 146.0, 1)
    conn.commit()
    records, unresolved = records_from_db(conn, "2026-09-18")
    assert unresolved == []
    settled = {r["currency"]: r for r in records if r["settlement_date"] == SETTLED}
    assert settled["USD"]["local_amount"] == 0.0 and settled["JPY"]["local_amount"] == 500_000.0
    assert settled["JPY"]["settled_on"] == "2026-09-10"
    rate = {"JPY": {"rate": 150.0, "inverted": True, "source": "TEST", "timestamp": "", "stale": False}}
    result = build_exposure(records, rate)
    assert list(result.ladder.index) == ["2026-09-25", SETTLED]  # engine sorts; the UI puts settled first
    assert result.ladder.loc[SETTLED, "JPY"] == 500_000.0
    jpy = result.summary.set_index("currency").loc["JPY"]
    assert jpy["local_delta"] == -292_000_000.0 + 500_000.0


def _option_db(as_of="2026-09-17", delta=0.4, spot=147.0, expiry="2026-11-19"):
    from data.ingest import schema
    conn = schema.connect()
    conn.execute("INSERT INTO instruments VALUES ('USDJPY','FX','USD','JPY',1,0,'USDJPY Curncy','9999-12-31')")
    conn.execute("INSERT INTO instruments VALUES ('USDJPY111926P-197571137','FX_OPTION','USD','JPY',1,0,"
                 "'USDJPY111926P-197571137','2026-11-19')")
    conn.execute("INSERT INTO trades VALUES ('o1','XLSX','USDJPY111926P-197571137','FX_OPTION','o1','2026-08-20',"
                 "-1e6,0.0125,'acc','cp','HAHY7','t','USDJPY put','')")
    conn.execute("INSERT INTO trade_legs VALUES ('o1',1,'NOTIONAL','USD',-1e6,'2026-08-20',?,0.0125,0)", (expiry,))
    if delta is not None:
        conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
                     (as_of, "USDJPY111926P-197571137", expiry, "DELTA", delta,
                      schema.OFFICIAL_MARK_SOURCE["DELTA"], f"{as_of}T17:00:00-04:00"))
    if spot is not None:
        conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
                     (as_of, "USDJPY", as_of, "SPOT", spot, "BBG_BFXFORWARD", f"{as_of}T17:00:00-04:00"))
    conn.commit()
    return conn


def test_option_delta_joins_ladder_as_two_records():
    """Short 1m USDJPY put with DELTA -0.4 (long-put convention; the trade's sign is in
    quantity): base-ccy delta = -1e6 x -0.4 = +400k USD, quote-ccy delta =
    -(-1e6) x -0.4 x 147 = -58.8m JPY, both dated at expiry, flagged non-cash, and the
    two legs net to zero USD at spot like any FX leg pair."""
    from engine.ladder.exposure_adapter import records_from_db
    conn = _option_db(delta=-0.4)
    records, unresolved = records_from_db(conn, "2026-09-17")
    assert unresolved == []
    by_ccy = {r["currency"]: r for r in records}
    assert set(by_ccy) == {"USD", "JPY"}
    assert by_ccy["USD"]["local_amount"] == pytest.approx(400_000.0)
    assert by_ccy["JPY"]["local_amount"] == pytest.approx(-58_800_000.0)
    assert all(r["product_type"] == "FX_OPTION" and r["settles_cash"] == 0
               and r["settlement_date"] == "2026-11-19" and r["currency_pair"] == "USDJPY" for r in records)
    exp = build_exposure(records, {"JPY": {"rate": 147.0, "inverted": True, "source": "t",
                                           "timestamp": "", "stale": False}})
    assert exp.summary["usd_delta"].sum() == pytest.approx(0.0, abs=1e-6)


def test_option_without_delta_or_spot_is_unresolved_never_dropped():
    from engine.ladder.exposure_adapter import records_from_db
    records, unresolved = records_from_db(_option_db(delta=None), "2026-09-17")
    assert records == [] and len(unresolved) == 1 and "no official DELTA" in unresolved[0].reason
    records, unresolved = records_from_db(_option_db(spot=None), "2026-09-17")
    assert records == [] and len(unresolved) == 1 and "no official SPOT for USDJPY" in unresolved[0].reason


def test_option_on_or_after_expiry_carries_no_delta():
    from engine.ladder.exposure_adapter import records_from_db, exposure_records_from_db
    conn = _option_db(as_of="2026-11-19", expiry="2026-11-19")
    assert records_from_db(conn, "2026-11-19") == ([], [])
    assert exposure_records_from_db(conn, "2026-11-19") == ([], [])
