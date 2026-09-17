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

    # grid rule (>=): a leg settling exactly on as_of is still shown (cash moving today).
    assert records_from_db(conn, "2026-09-16")[0] != []
    # exposure rule (>): that same leg carries no delta by close of settlement day.
    assert exposure_records_from_db(conn, "2026-09-16") == ([], [])
    # both rules agree once truly settled.
    assert records_from_db(conn, "2026-09-17") == ([], [])


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
