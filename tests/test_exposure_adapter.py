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


def test_fx_legs_sit_on_their_own_value_date_and_settle_as_cash():
    """Every FX leg is dated on its own value date (the NDF fixing-date rule left on
    2026-09-24): a USDCNH forward valued Wed 2026-11-18 is on the grid under that date
    up to and including it, and from the day after it is settled cash in both
    currencies, still carrying delta."""
    from engine.ladder.exposure_adapter import SETTLED, records_from_db, exposure_records_from_db
    conn = _fresh()
    conn.execute("INSERT INTO instruments VALUES ('USDCNH','FX','USD','CNH',1,0,'USDCNH Curncy','9999-12-31')")
    _trade(conn, "c1", "USDCNH", "FX_FWD", -1_500_000.0, 7.1425, trade_date="2026-08-05")
    _leg(conn, "c1", 1, "FX_NEAR", "USD", -1_500_000.0, "2026-11-18", 7.1425, 1)
    _leg(conn, "c1", 2, "FX_NEAR", "CNH", 10_713_750.0, "2026-11-18", 7.1425, 1)
    conn.commit()

    for day in ("2026-11-13", "2026-11-16", "2026-11-18"):   # the fixing-date rule would have moved it
        grid, named = records_from_db(conn, day)
        assert named == []
        assert {(r["currency"], r["settlement_date"]) for r in grid} == {("USD", "2026-11-18"), ("CNH", "2026-11-18")}
        assert all(r["settles_cash"] == 1 and "fixing_date" not in r and "is_ndf" not in r for r in grid)
    exposure, _ = exposure_records_from_db(conn, "2026-11-17")
    assert {r["settlement_date"] for r in exposure} == {"2026-11-18"}
    for fn in (records_from_db, exposure_records_from_db):
        settled, named = fn(conn, "2026-11-19")
        assert named == []
        assert {(r["currency"], r["settlement_date"], r["local_amount"]) for r in settled} == {
            ("USD", SETTLED, -1_500_000.0), ("CNH", SETTLED, 10_713_750.0)}
        assert all(r["settled_on"] == "2026-11-18" for r in settled)


def test_settled_future_brings_its_realised_usd_or_is_named():
    """A future never delivers: once expired, its cash is the USD settlement the ledger
    froze in realised_pnl, read back as it stands; before the ledger has frozen it, it is
    named, never valued."""
    from engine.ladder.exposure_adapter import SETTLED, records_from_db
    conn = _fresh()
    conn.execute("INSERT INTO instruments VALUES ('CLZ6 Comdty','FUTURE','CL','USD',1000,0,'CLZ6 Comdty','2026-11-20')")
    _trade(conn, "f1", "CLZ6 Comdty", "FUTURE", 10.0, 68.45)
    _leg(conn, "f1", 1, "NOTIONAL", "USD", 684_500.0, "2026-11-20", 68.45, 0)
    conn.commit()
    records, named = records_from_db(conn, "2026-11-23")
    assert records == [] and len(named) == 1
    assert named[0].trade_id == "f1" and named[0].reason.startswith("settled 2026-11-20 (FUTURE), USD settlement unknown")

    conn.execute("INSERT INTO realised_pnl (trade_id, instrument_id, product, currency, settle_date, local_amount,"
                 " usd_entry_amount, mark_type, spot_usd_per_local, spot_as_of_date, spot_source, pnl_usd,"
                 " frozen_at, note) VALUES ('f1','CLZ6 Comdty','FUTURE','USD','2026-11-20',0.0,0.0,'FUTURE_PX',"
                 " 1.0,'2026-11-20','BBG_BDH',12_500.0,'2026-11-21T00:00:00+00:00','')")
    conn.commit()
    records, named = records_from_db(conn, "2026-11-23")
    assert named == []
    assert [(r["currency"], r["settlement_date"], r["local_amount"]) for r in records] == [("USD", SETTLED, 12_500.0)]


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


# ------------------------------------------------------------------ LME forwards (Phase 5)
def _lme_db(with_fx=True, with_lme=True):
    """A bought copper LME forward (100 t at 9,850, prompt Wed 2026-12-16) in the shape
    ingest-parser writes: the metal leg in the root id's name, settles_cash 0, and the
    USD leg -tonnes x fill, settles_cash 1. Beside it a USDJPY forward on the same date."""
    conn = _fresh()
    if with_lme:
        conn.execute("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf,"
                     " bbg_ticker, expiry_date) VALUES ('LME:CA','LME_FWD','LME:CA','USD',1,0,'LMCADY Comdty',"
                     "'9999-12-31')")
        _trade(conn, "L1", "LME:CA", "LME_FWD", 100.0, 9_850.0, trade_date="2026-09-10")
        _leg(conn, "L1", 1, "FX_NEAR", "LME:CA", 100.0, "2026-12-16", 9_850.0, 0)
        _leg(conn, "L1", 2, "FX_NEAR", "USD", -985_000.0, "2026-12-16", 9_850.0, 1)
    if with_fx:
        conn.execute("INSERT INTO instruments VALUES ('USDJPY','FX','USD','JPY',1,0,'USDJPY Curncy','9999-12-31')")
        _trade(conn, "F1", "USDJPY", "FX_FWD", 1_000_000.0, 147.0, trade_date="2026-09-10")
        _leg(conn, "F1", 1, "FX_NEAR", "USD", 1_000_000.0, "2026-12-16", 147.0, 1)
        _leg(conn, "F1", 2, "FX_NEAR", "JPY", -147_000_000.0, "2026-12-16", 147.0, 1)
    conn.commit()
    return conn


def _lme_rows(records):
    return sorted((r["currency"], r["settlement_date"], r["local_amount"])
                  for r in records if r["trade_id"] == "L1")


def test_lme_usd_leg_is_cash_on_its_prompt_and_the_metal_leg_is_never_a_currency():
    """CLAUDE.md "LME forwards": the cash lands on the ladder on the prompt date. The USD
    leg is on the grid under the prompt (up to and including it) and is an open record
    under both rules before it; the metal leg is on neither and is not called unresolved
    either (it is a Curve-tab position, not a dropped trade)."""
    from engine.ladder.exposure_adapter import records_from_db, exposure_records_from_db
    from engine.ladder.ladder import cash_ladder
    conn = _lme_db()
    for day in ("2026-09-14", "2026-12-15", "2026-12-16"):
        grid = cash_ladder(conn, day)
        assert "LME:CA" not in set(grid["ccy"])
        usd = grid[grid["ccy"] == "USD"].set_index("settle_date")["amount"].to_dict()
        assert usd == {"2026-12-16": 1_000_000.0 - 985_000.0}
        records, named = records_from_db(conn, day)
        assert named == []
        assert _lme_rows(records) == [("USD", "2026-12-16", -985_000.0)]
        assert all(r["settles_cash"] == 1 and r["product_type"] == "LME_FWD"
                   for r in records if r["trade_id"] == "L1")
        assert "LME:CA" not in {r["currency"] for r in records}
    exposure, named = exposure_records_from_db(conn, "2026-12-15")
    assert named == [] and _lme_rows(exposure) == [("USD", "2026-12-16", -985_000.0)]


def test_lme_usd_leg_after_the_prompt_is_settled_usd_cash_not_its_realised_pnl():
    """From the day after the prompt (grid) or by the prompt's close (exposure) the USD
    leg is settled cash in USD, like a deliverable FX leg; the metal leg is not settled
    cash, and the ledger's realised P&L of the ticket is never added on top (the USD leg
    already is the cash), nor is the ticket named as an unknown settlement."""
    from engine.ladder.exposure_adapter import SETTLED, records_from_db, exposure_records_from_db
    from engine.ladder.ladder import cash_ladder
    conn = _lme_db()
    conn.execute("INSERT INTO realised_pnl (trade_id, instrument_id, product, currency, settle_date, local_amount,"
                 " usd_entry_amount, mark_type, spot_usd_per_local, spot_as_of_date, spot_source, pnl_usd,"
                 " frozen_at, note) VALUES ('L1','LME:CA','LME_FWD','USD','2026-12-16',15_000.0,-985_000.0,"
                 "'FWD_OUTRIGHT',1.0,'2026-12-16','BBG_BFXFORWARD',15_000.0,'2026-12-17T00:00:00+00:00','')")
    conn.commit()
    exposure_on_prompt, named = exposure_records_from_db(conn, "2026-12-16")
    assert named == [] and _lme_rows(exposure_on_prompt) == [("USD", SETTLED, -985_000.0)]
    for fn in (records_from_db, exposure_records_from_db):
        settled, named = fn(conn, "2026-12-17")
        assert named == []
        assert _lme_rows(settled) == [("USD", SETTLED, -985_000.0)]
        assert [r["settled_on"] for r in settled if r["trade_id"] == "L1"] == ["2026-12-16"]
        assert "LME:CA" not in {r["currency"] for r in settled}
    assert cash_ladder(conn, "2026-12-17").empty


def test_lme_forward_adds_nothing_to_currency_delta_or_fx_net_gross():
    """The metal leg is never currency delta: not in delta_per_ccy (LME_FWD is not in the
    contract SQL's product list, so its USD leg is left out there too), not a row of the
    exposure summary. The USD leg sits in the USD row, which Net / Gross USD leave out,
    so FX Net / Gross are exactly the FX forward's."""
    from engine.ladder.exposure import build_exposure, portfolio_totals
    from engine.ladder.exposure_adapter import exposure_records_from_db
    from engine.ladder.ladder import delta_per_ccy
    rate = {"JPY": {"rate": 150.0, "inverted": True, "source": "TEST", "timestamp": "", "stale": False}}
    with_lme, only_fx = _lme_db(), _lme_db(with_lme=False)
    delta = delta_per_ccy(with_lme, "2026-09-14").set_index("ccy")["delta"].to_dict()
    assert delta == delta_per_ccy(only_fx, "2026-09-14").set_index("ccy")["delta"].to_dict()
    assert delta == {"JPY": -147_000_000.0, "USD": 1_000_000.0}
    records, _ = exposure_records_from_db(with_lme, "2026-09-14")
    result = build_exposure(records, rate)
    summary = result.summary.set_index("currency")
    assert set(summary.index) == {"USD", "JPY"}
    assert summary.loc["USD", "local_delta"] == 1_000_000.0 - 985_000.0
    fx_records, _ = exposure_records_from_db(only_fx, "2026-09-14")
    totals, fx_totals = portfolio_totals(result), portfolio_totals(build_exposure(fx_records, rate))
    assert (totals["net_usd"], totals["gross_usd"]) == (fx_totals["net_usd"], fx_totals["gross_usd"])


def test_fx_forward_records_unchanged_by_an_lme_ticket_beside_it():
    from engine.ladder.exposure_adapter import records_from_db, exposure_records_from_db

    def fx(records):
        return [r for r in records if r["trade_id"] == "F1"]
    for day in ("2026-09-14", "2026-12-16", "2026-12-17"):
        for fn in (records_from_db, exposure_records_from_db):
            assert fx(fn(_lme_db(), day)[0]) == fx(fn(_lme_db(with_lme=False), day)[0])


# ------------------------------------------------------------ listed options (Phase 5)
def _cmdty_option_db():
    """A long SHFE-style copper call on a commodity future (CMDTY_OPTION, the shape
    ingest-parser writes): one NOTIONAL leg in the quote currency on its expiry,
    settles_cash 0."""
    conn = _fresh()
    conn.execute("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf,"
                 " bbg_ticker, expiry_date) VALUES ('HGZ6C 450 Comdty','CMDTY_OPTION','COMEX:HG','USD',25000,0,"
                 "'HGZ6C 450 Comdty','2026-11-24')")
    _trade(conn, "O1", "HGZ6C 450 Comdty", "CMDTY_OPTION", 2.0, 0.12)
    _leg(conn, "O1", 1, "NOTIONAL", "USD", 6_000.0, "2026-11-24", 0.12, 0)
    conn.commit()
    return conn


def test_open_cmdty_option_is_no_currency_record_and_not_named():
    """An open listed option is no currency exposure (its delta is on the Curve tab): no
    record, and no "non-FX product excluded" line under either rule."""
    from engine.ladder.exposure_adapter import records_from_db, exposure_records_from_db
    conn = _cmdty_option_db()
    for fn in (records_from_db, exposure_records_from_db):
        assert fn(conn, "2026-10-01") == ([], [])


def test_settled_cmdty_option_brings_its_realised_usd_or_is_named():
    """Once expired a listed option settles like a future: named while the ledger has not
    frozen it, then its USD settlement read from realised_pnl as it stands."""
    from engine.ladder.exposure_adapter import SETTLED, records_from_db, exposure_records_from_db
    conn = _cmdty_option_db()
    for fn in (records_from_db, exposure_records_from_db):
        records, named = fn(conn, "2026-11-25")
        assert records == [] and len(named) == 1
        assert named[0].trade_id == "O1"
        assert named[0].reason.startswith("settled 2026-11-24 (CMDTY_OPTION), USD settlement unknown")

    conn.execute("INSERT INTO realised_pnl (trade_id, instrument_id, product, currency, settle_date, local_amount,"
                 " usd_entry_amount, mark_type, spot_usd_per_local, spot_as_of_date, spot_source, pnl_usd,"
                 " frozen_at, note) VALUES ('O1','HGZ6C 450 Comdty','CMDTY_OPTION','USD','2026-11-24',4_250.0,"
                 "6_000.0,'FUTURE_PX',1.0,'2026-11-24','BBG_BDH',4_250.0,'2026-11-25T00:00:00+00:00','')")
    conn.commit()
    for fn in (records_from_db, exposure_records_from_db):
        records, named = fn(conn, "2026-11-25")
        assert named == []
        assert [(r["currency"], r["settlement_date"], r["local_amount"], r["product_type"], r["settled_on"])
                for r in records] == [("USD", SETTLED, 4_250.0, "CMDTY_OPTION", "2026-11-24")]
