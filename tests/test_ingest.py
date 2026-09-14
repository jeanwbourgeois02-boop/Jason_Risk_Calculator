"""Tests for data/ingest (schema + BNP parser). Real-file tests skip if the raw file is absent."""
from __future__ import annotations

import csv
import logging
import math
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from data.ingest import bnp, schema

REPO = Path(__file__).resolve().parents[1]
RAW = REPO / "data" / "raw" / "HA_PNL_20260818.csv"

needs_raw = pytest.mark.skipif(not RAW.exists(), reason=f"raw file absent: {RAW}")


# --------------------------------------------------------------------------- schema
def test_schema_creates_tables_and_view():
    conn = schema.connect()
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(schema.TABLES) <= names
    views = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
    assert "marks_official" in views
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    # idempotent
    schema.create_schema(conn)
    schema.create_schema(conn)


def test_schema_columns_match_contract():
    conn = schema.connect()
    cols = lambda t: [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
    assert cols("instruments") == ["instrument_id", "asset_class", "base_ccy", "quote_ccy", "multiplier",
                                   "is_ndf", "bbg_ticker", "expiry_date"]
    assert cols("trades") == ["trade_id", "source", "instrument_id", "product", "package_id", "trade_date",
                              "quantity", "price", "account", "counterparty", "strategy", "trader", "description"]
    assert cols("trade_legs") == ["trade_id", "leg_no", "leg_type", "ccy", "amount", "start_date", "settle_date",
                                  "rate", "settles_cash"]
    assert cols("marks") == ["as_of_date", "instrument_id", "settle_date", "mark_type", "value", "source",
                             "snapped_at"]
    assert cols("curves") == ["curve_id", "as_of_date", "node_date", "discount_factor", "par_rate", "source"]
    assert cols("positions") == ["as_of_date", "source", "account", "instrument_id", "settle_date", "quantity",
                                 "cost_local", "mark", "fx_to_usd", "mv_local", "mv_usd", "pnl_dtd_usd",
                                 "pnl_mtd_usd", "pnl_ytd_usd"]
    # every column NOT NULL
    for t in schema.TABLES:
        for r in conn.execute(f"PRAGMA table_info({t})"):
            assert r[3] == 1 or r[5] == 1, f"{t}.{r[1]} is nullable"


def test_schema_foreign_keys_enforced():
    conn = schema.connect()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO trades VALUES ('t1','BNP','NOPE','FX_FWD','t1','2026-08-17',1,1,'a','c','s','tr','d')")


def test_marks_official_filters_to_official_source():
    conn = schema.connect()
    conn.execute("INSERT INTO instruments VALUES ('USDJPY','FX','USD','JPY',1,0,'USDJPY Curncy','9999-12-31')")
    rows = [
        ("2026-08-17", "USDJPY", "2026-08-17", "SPOT", 147.10, "BNP_BVAL", "2026-08-17T15:00:00-04:00"),
        ("2026-08-17", "USDJPY", "2026-08-17", "SPOT", 147.12, "BBG_BFXFORWARD", "2026-08-17T15:00:00-04:00"),
        ("2026-08-17", "USDJPY", "2026-08-17", "DELTA", 0.5, "BBG_BFXFORWARD", "2026-08-17T15:00:00-04:00"),
        ("2026-08-17", "USDJPY", "2026-08-17", "DELTA", 0.6, "MANUAL", "2026-08-17T15:00:00-04:00"),
        ("2026-08-17", "USDJPY", "2026-08-17", "PV_USD", 1.0, "BNP_BVAL", "2026-08-17T15:00:00-04:00"),
    ]
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", rows)
    assert conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0] == 5
    got = conn.execute(
        "SELECT mark_type, value, source FROM marks_official ORDER BY mark_type").fetchall()
    assert got == [("DELTA", 0.6, "MANUAL"), ("SPOT", 147.12, "BBG_BFXFORWARD")]


# --------------------------------------------------------------------------- helpers
def test_previous_weekday_is_not_a_holiday_calendar():
    assert bnp._previous_weekday(date(2026, 8, 18)) == date(2026, 8, 17)  # Tue -> Mon
    assert bnp._previous_weekday(date(2026, 8, 17)) == date(2026, 8, 14)  # Mon -> Fri
    assert bnp._previous_weekday(date(2026, 8, 16)) == date(2026, 8, 14)  # Sun -> Fri
    # Tuesday after Labor Day 2026 (Mon 2026-09-07): no holiday awareness, caller must override.
    assert bnp._previous_weekday(date(2026, 9, 8)) == date(2026, 9, 7)
    assert not hasattr(bnp, "previous_business_day")


def test_file_date_from_name():
    assert bnp.file_date_from_name("x/HA_PNL_20260818.csv") == date(2026, 8, 18)
    with pytest.raises(ValueError):
        bnp.file_date_from_name("other.csv")


def test_cash_ccy_mapping():
    assert bnp.cash_ccy("DOL.C-USAA") == "USD"
    assert bnp.cash_ccy("TRY.C-TIAA") == "TRY"
    assert bnp.cash_ccy("XAU.C-XAAA") == "XAU"
    with pytest.raises(ValueError):
        bnp.cash_ccy("USD")


def test_future_expiry_third_friday():
    root, code, exp = bnp.future_expiry("ESU6-USAA", date(2026, 8, 17))
    assert (root, code, exp) == ("ES", "ESU6", date(2026, 9, 18))
    assert bnp.future_expiry("ESZ6-USAA", date(2026, 8, 17))[2] == date(2026, 12, 18)
    assert bnp.future_expiry("ESH7-USAA", date(2026, 8, 17))[2] == date(2027, 3, 19)
    # year digit rollover: '0' seen from 2029 means 2030
    assert bnp.future_expiry("ESM0-USAA", date(2029, 8, 17))[2].year == 2030


def test_future_expiry_rejects_unknown_root():
    with pytest.raises(ValueError, match="unknown futures root"):
        bnp.future_expiry("NQU6-USAA", date(2026, 8, 17))
    with pytest.raises(ValueError, match="unrecognised futures symbol"):
        bnp.future_expiry("ES-USAA", date(2026, 8, 17))


def test_description_regex_accepts_both_orders_and_rejects_variants():
    ok1 = "TD 08/03/2026 VD 09/16/2026 SELL USD VS .BUY TRY @ 49.21249500"
    ok2 = "TD 08/03/2026 VD 09/16/2026 BUY USD VS .SELL TRY @ 49.21249500"
    assert bnp.DESCRIPTION_RE.match(ok1)
    assert bnp.DESCRIPTION_RE.match(ok2)
    assert not bnp.DESCRIPTION_RE.match(ok1.replace(" .BUY", " BUY"))  # dot is mandatory
    assert not bnp.DESCRIPTION_RE.match(ok1[:-1])  # 7 decimals


def test_recon_nan_deviation_is_failure_and_propagates_to_max():
    rep = bnp.ReconReport()
    rep.add("c", 2, "X", 0.5, 1.0)
    rep.add("c", 3, "Y", math.nan, 1.0)
    rep.add("d", 4, "Z", 0.0, 0.0)
    assert [f.row_no for f in rep.failures] == [3]
    md = rep.max_deviation()
    assert math.isnan(md["c"]) and md["d"] == 0.0


# --------------------------------------------------------------------------- synthetic file
# Always the minimal header so synthetic tests behave the same with or without the raw file.
_MIN_HEADER = ["Account", "CounterParty", "Currency", "Cost", "Fx", "Local Cost", "Market Value Base",
               "Market Value Local", "Position", "Price", "Quantity", "Symbol", "Symbol Description",
               "Trade Factor", "NM Strategy", "Trader Name", "Fund", "Financial Type", "Start Date Dirty MV",
               "Previous Month End Market Value Base", "DTD Total P&L", "DTD Trading P&L", "MTD Total P&L",
               "YTD Total P&L"]


def _write_csv(path: Path, rows: list[dict]) -> Path:
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_MIN_HEADER)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in _MIN_HEADER})
    return path


def _fwd_row(**over):
    base = dict(
        Account="BNPP-IPBFX-NMMF", CounterParty="PARIUK", Currency="JPY.C-JPAA", Fund="NMMF",
        **{"Financial Type": "FORWARD", "NM Strategy": "HAHY7", "Trader Name": "Henry Chan",
           "Trade Factor": 1.0, "Local Cost": 147000000.0, "Market Value Local": 500000.0,
           "Market Value Base": 3401.36, "Start Date Dirty MV": 3000.0,
           "Previous Month End Market Value Base": 0.0, "DTD Total P&L": 401.36, "DTD Trading P&L": 401.36,
           "MTD Total P&L": 3401.36, "YTD Total P&L": 3401.36, "Symbol Description":
           "TD 08/03/2026 VD 09/16/2026 BUY USD VS .SELL JPY @ 147.00000000"},
        Cost=-1000000.0, Fx=0.0068027, Position=1000000.0, Price=147.5, Quantity=1000000.0,
        Symbol="USDJPY091626-111",
    )
    base.update(over)
    return base


def _fut_row(**over):
    base = dict(
        Account="GSIL-FUT-NMMF", CounterParty="GSIL", Currency="DOL.C-USAA", Fund="NMMF",
        **{"Financial Type": "FUTURES", "NM Strategy": "HACA", "Trader Name": "Henry Chan",
           "Trade Factor": 50.0, "Local Cost": 10234475.0, "Market Value Local": 253337.5,
           "Market Value Base": 253337.5, "Symbol Description": "E-MINI S&P SEP26",
           "DTD Total P&L": -48937.5, "MTD Total P&L": 0.0, "YTD Total P&L": 0.0},
        Cost=10234475.0, Fx=1.0, Position=0.0, Price=7768.75, Quantity=27.0, Symbol="ESU6-USAA",
    )
    base.update(over)
    return base


def _cash_row(**over):
    base = dict(
        Account="BNPP-IPBFX-NMMF", CounterParty="", Currency="TRY.C-TIAA", Fund="NMMF",
        **{"Financial Type": "CURRENCY", "NM Strategy": "HACA", "Trader Name": "", "Trade Factor": 1.0,
           "Local Cost": 0.0, "Market Value Local": 1000.0, "Market Value Base": 20.0,
           "Symbol Description": "TURKISH LIRA"},
        Cost=0.0, Fx=0.02, Position=1000.0, Price=1.0, Quantity=1000.0, Symbol="TRY.C-TIAA",
    )
    base.update(over)
    return base


def test_synthetic_reject_reports_row_number(tmp_path):
    p = _write_csv(tmp_path / "HA_PNL_20260818.csv", [
        _fwd_row(),
        _fwd_row(Symbol="USDJPY091626-112",
                 **{"Symbol Description": "TD 08/03/2026 VD 09/16/2026 BUY USD VS SELL JPY @ 147.00000000"}),
        _fwd_row(Symbol="USDJPY091726-113"),  # VD in symbol disagrees with description
        _fwd_row(Symbol="USDCHF091626-114"),  # pair disagrees with description
        _fwd_row(Symbol="USDJPY091626-115", Fund="OTHER"),
        _fwd_row(Symbol="IRSOIS-USD-1", **{"Financial Type": "INTEREST_RATE_SWAP"}),
    ])
    r = bnp.parse(p)
    assert r.as_of_date == "2026-08-17"
    assert [t.trade_id for t in r.trades] == ["111"]
    assert sorted((x.row_no, x.symbol) for x in r.rejects) == [
        (3, "USDJPY091626-112"), (4, "USDJPY091726-113"), (5, "USDCHF091626-114")]
    assert "regex" in next(x.reason for x in r.rejects if x.row_no == 3)
    assert r.n_skipped_fund == 1 and r.n_skipped_irs == 1


def test_synthetic_leg_layout_and_direction(tmp_path):
    """Both verb orders map to the same legs; quantity sign follows the base currency."""
    p = _write_csv(tmp_path / "HA_PNL_20260818.csv", [
        _fwd_row(),
        _fwd_row(Symbol="USDJPY091626-222", Quantity=-1000000.0, Position=-1000000.0,
                 **{"Local Cost": -147000000.0, "Symbol Description":
                    "TD 08/03/2026 VD 09/16/2026 SELL USD VS .BUY JPY @ 147.00000000",
                    "Market Value Local": -500000.0, "Market Value Base": -3401.36,
                    "Start Date Dirty MV": -3000.0, "DTD Total P&L": -401.36, "DTD Trading P&L": -401.36,
                    "MTD Total P&L": -3401.36}),
    ])
    r = bnp.parse(p)
    assert not r.rejects
    legs = {l.trade_id: sorted([x for x in r.legs if x.trade_id == l.trade_id], key=lambda x: x.leg_no)
            for l in r.legs}
    a, b = legs["111"], legs["222"]
    assert (a[0].ccy, a[0].amount, a[1].ccy, a[1].amount) == ("USD", 1000000.0, "JPY", -147000000.0)
    assert (b[0].ccy, b[0].amount, b[1].ccy, b[1].amount) == ("USD", -1000000.0, "JPY", 147000000.0)
    assert all(x.settle_date == "2026-09-16" and x.start_date == "2026-08-03" and x.settles_cash == 1 for x in a + b)
    assert r.recon.max_deviation()["direction_sign"] == 0.0
    # netted into one positions row per PK
    assert len(r.positions) == 1 and r.positions[0].quantity == 0.0
    assert r.recon.passed


def test_synthetic_futures_row_writes_position_only(tmp_path):
    """C1: a FUTURES row never becomes a trade (no fill date / price in the snapshot)."""
    p = _write_csv(tmp_path / "HA_PNL_20260818.csv", [_fut_row()])
    r = bnp.parse(p)
    assert not r.rejects and r.recon.passed
    assert r.trades == [] and r.legs == []
    es = r.instruments["ESU6 Index"]
    assert (es.asset_class, es.base_ccy, es.quote_ccy, es.multiplier, es.expiry_date) == \
        ("FUTURE", "ES", "USD", 50.0, "2026-09-18")
    (pos,) = r.positions
    assert (pos.instrument_id, pos.settle_date, pos.quantity, pos.mark, pos.fx_to_usd, pos.mv_usd) == \
        ("ESU6 Index", "2026-09-18", 27.0, 7768.75, 1.0, 253337.5)
    # a second daily load must not collide on trades (nothing written there)
    conn = schema.connect()
    bnp.load(p, conn)
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 1


# ---- W7: each recon check / reject path can actually fail
def test_synthetic_local_cost_off_by_two_fails(tmp_path):
    p = _write_csv(tmp_path / "HA_PNL_20260818.csv", [_fwd_row(**{"Local Cost": 147000002.0})])
    r = bnp.parse(p)
    fails = {f.check: f for f in r.recon.failures}
    assert set(fails) == {"local_cost"}
    assert fails["local_cost"].row_no == 2 and fails["local_cost"].deviation == pytest.approx(2.0)
    assert r.recon.max_deviation()["local_cost"] == pytest.approx(2.0)


def test_synthetic_netting_mark_mismatch_fails_with_row_numbers(tmp_path):
    """Same account / pair / value date but different Price: netting is unsafe and says which rows."""
    p = _write_csv(tmp_path / "HA_PNL_20260818.csv", [
        _fwd_row(),
        _fwd_row(Symbol="USDJPY091626-333", Price=148.0,
                 **{"Market Value Local": 1000000.0, "Market Value Base": 6802.7, "Start Date Dirty MV": 6401.34,
                    "DTD Total P&L": 401.36, "DTD Trading P&L": 401.36, "MTD Total P&L": 6802.7}),
    ])
    r = bnp.parse(p)
    assert not r.rejects
    fails = [f for f in r.recon.failures]
    assert [f.check for f in fails] == ["positions_net_mark_consistent"]
    f = fails[0]
    assert f.row_no == 2 and f.deviation == pytest.approx(0.5)
    assert "rows [2, 3]" in f.detail and "148.0" in f.detail
    # fx identical so the fx check passes
    assert r.recon.max_deviation()["positions_net_fx_consistent"] == 0.0


def test_synthetic_futures_blank_trade_factor_is_rejected(tmp_path):
    p = _write_csv(tmp_path / "HA_PNL_20260818.csv", [_fut_row(**{"Trade Factor": ""})])
    r = bnp.parse(p)
    assert [(x.row_no, x.symbol) for x in r.rejects] == [(2, "ESU6-USAA")]
    assert "Trade Factor" in r.rejects[0].reason
    assert r.positions == [] and r.trades == [] and "ESU6 Index" not in r.instruments


def test_synthetic_futures_unknown_root_is_rejected(tmp_path):
    p = _write_csv(tmp_path / "HA_PNL_20260818.csv", [_fut_row(Symbol="NQU6-USAA")])
    r = bnp.parse(p)
    assert len(r.rejects) == 1 and "unknown futures root" in r.rejects[0].reason


def test_synthetic_currency_blank_fx_sentinel(tmp_path, caplog):
    p = _write_csv(tmp_path / "HA_PNL_20260818.csv", [
        _cash_row(Fx="", Quantity=0.0, Position=0.0, **{"Market Value Local": 0.0, "Market Value Base": 0.0}),
        _cash_row(Symbol="DOL.C-USAA", Currency="DOL.C-USAA", Fx="", Quantity=5.0,
                  **{"Market Value Local": 5.0, "Market Value Base": 5.0}),
        _cash_row(Symbol="XAU.C-XAAA", Currency="XAU.C-XAAA", Fx=3300.0, Quantity=2.0,
                  **{"Market Value Local": 2.0, "Market Value Base": 6600.0}),
    ])
    with caplog.at_level(logging.INFO, logger="data.ingest.bnp"):
        r = bnp.parse(p)
    assert not r.rejects
    by_id = {x.instrument_id: x for x in r.positions}
    assert by_id["CASH-TRY"].fx_to_usd == 0.0  # sentinel for blank Fx on a non-USD balance
    assert by_id["CASH-USD"].fx_to_usd == 1.0  # USD is 1 by definition even when blank
    assert by_id["CASH-XAU"].fx_to_usd == 3300.0
    assert all(x.settle_date == "2026-08-17" for x in r.positions)
    assert any("blank Fx" in m for m in caplog.messages)


def test_synthetic_blank_price_reports_nan_failure(tmp_path):
    """W4: a blank Price cell must surface as a failure, not vanish inside max()."""
    p = _write_csv(tmp_path / "HA_PNL_20260818.csv", [_fwd_row(), _fwd_row(Symbol="USDJPY091726-444", Price="",
                                                               **{"Symbol Description":
                                                                  "TD 08/03/2026 VD 09/17/2026 BUY USD VS .SELL JPY @ 147.00000000"})])
    r = bnp.parse(p)
    assert not r.rejects  # blank numeric cells are recon failures, not parse rejects
    fails = {(f.check, f.row_no) for f in r.recon.failures}
    assert ("mv_local", 3) in fails
    assert all(math.isnan(f.deviation) for f in r.recon.failures)
    assert math.isnan(r.recon.max_deviation()["mv_local"])
    assert not r.recon.passed


def test_synthetic_blank_fx_on_forward_reports_nan_failure(tmp_path):
    p = _write_csv(tmp_path / "HA_PNL_20260818.csv", [_fwd_row(Fx="")])
    r = bnp.parse(p)
    assert [f.check for f in r.recon.failures] == ["mv_base"]
    assert math.isnan(r.recon.max_deviation()["mv_base"])


# ---- W3: load() strictness
def test_load_strict_raises_on_reject_and_writes_nothing(tmp_path, caplog):
    p = _write_csv(tmp_path / "HA_PNL_20260818.csv", [
        _fwd_row(),
        _fwd_row(Symbol="USDJPY091626-112",
                 **{"Symbol Description": "TD 08/03/2026 VD 09/16/2026 BUY USD VS SELL JPY @ 147.00000000"}),
    ])
    conn = schema.connect()
    with caplog.at_level(logging.WARNING, logger="data.ingest.bnp"):
        with pytest.raises(ValueError, match=r"1 reject\(s\), 0 recon failure\(s\)") as ei:
            bnp.load(p, conn)
    assert "USDJPY091626-112" in str(ei.value)
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
    assert any("REJECT" in m for m in caplog.messages)


def test_load_strict_raises_on_recon_failure(tmp_path):
    p = _write_csv(tmp_path / "HA_PNL_20260818.csv", [_fwd_row(**{"Local Cost": 147000002.0})])
    conn = schema.connect()
    with pytest.raises(ValueError, match=r"0 reject\(s\), 1 recon failure\(s\)") as ei:
        bnp.load(p, conn)
    assert "local_cost" in str(ei.value)
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0


def test_load_non_strict_loads_good_rows_and_logs(tmp_path, caplog):
    p = _write_csv(tmp_path / "HA_PNL_20260818.csv", [
        _fwd_row(),
        _fwd_row(Symbol="USDJPY091626-112",
                 **{"Symbol Description": "TD 08/03/2026 VD 09/16/2026 BUY USD VS SELL JPY @ 147.00000000"}),
        _fwd_row(Symbol="USDJPY091726-113", **{"Local Cost": 147000002.0, "Symbol Description":
                                              "TD 08/03/2026 VD 09/17/2026 BUY USD VS .SELL JPY @ 147.00000000"}),
    ])
    conn = schema.connect()
    with caplog.at_level(logging.WARNING, logger="data.ingest.bnp"):
        r = bnp.load(p, conn, strict=False)
    assert len(r.rejects) == 1 and len(r.recon.failures) == 1
    # the rejected row is never inserted; the recon-failing row is (non-strict)
    assert sorted(x[0] for x in conn.execute("SELECT trade_id FROM trades")) == ["111", "113"]
    warnings = [m for m in caplog.messages]
    assert any("REJECT" in m and "row 3" in m for m in warnings)
    assert any("local_cost FAIL" in m and "row 4" in m for m in warnings)


def test_load_rejects_duplicate_trades(tmp_path):
    p = _write_csv(tmp_path / "HA_PNL_20260818.csv", [_fwd_row()])
    conn = schema.connect()
    bnp.load(p, conn)
    with pytest.raises(sqlite3.IntegrityError):
        bnp.load(p, conn)


# --------------------------------------------------------------------------- real file
@pytest.fixture(scope="module")
def parsed():
    if not RAW.exists():
        pytest.skip("raw file absent")
    return bnp.parse(RAW)


@pytest.fixture(scope="module")
def raw_df():
    if not RAW.exists():
        pytest.skip("raw file absent")
    return pd.read_csv(RAW)


@needs_raw
def test_real_counts(parsed):
    assert parsed.as_of_date == "2026-08-17"
    assert parsed.n_forward == 229
    assert parsed.rejects == []
    assert parsed.n_skipped_irs == 3
    assert parsed.n_currency == 9 and parsed.n_futures == 1
    assert len(parsed.trades) == 229
    assert all(t.product == "FX_FWD" for t in parsed.trades)
    assert len(parsed.legs) == 229 * 2
    assert all(l.leg_type == "FX_NEAR" for l in parsed.legs)
    assert all(t.source == "BNP" and t.package_id == t.trade_id for t in parsed.trades)
    assert {t.strategy for t in parsed.trades} <= {"HAHY7", "HACA"}


@needs_raw
def test_real_all_tolerances_met(parsed):
    rep = parsed.recon
    assert rep.failures == [], "\n".join(map(str, rep.failures[:20]))
    md = rep.max_deviation()
    for check in bnp.CONTRACT_CHECKS | {"direction_sign", "future_mv", "positions_net_mark_consistent"}:
        assert check in md, check
    assert not any(math.isnan(v) for v in md.values())
    assert len(rep.by_check()["local_cost"]) == 229
    assert md["local_cost"] <= bnp.TOL_LOCAL_COST
    assert md["mv_local"] <= bnp.TOL_MV_LOCAL
    assert md["dtd_total_pnl"] <= bnp.PNL_TOL and md["mtd_total_pnl"] <= bnp.PNL_TOL
    # netting checks cite real row numbers, never 0
    assert all(r.row_no >= 2 for r in rep.by_check()["positions_net_mark_consistent"])


@needs_raw
def test_real_sample_forward_legs(parsed):
    t = next(t for t in parsed.trades if t.trade_id == "196789440")
    assert t.instrument_id == "USDTRY" and t.trade_date == "2026-08-03" and t.price == 49.212495
    assert t.quantity == -1000000.0
    legs = sorted((l for l in parsed.legs if l.trade_id == t.trade_id), key=lambda l: l.leg_no)
    assert len(legs) == 2
    assert legs[0].ccy == "USD" and legs[0].amount == -1000000.0
    assert legs[1].ccy == "TRY" and legs[1].amount == 49212495.0
    assert all(l.settle_date == "2026-09-16" and l.rate == 49.212495 and l.leg_type == "FX_NEAR" for l in legs)
    assert all(l.settles_cash == 1 for l in legs)  # TRY deliverable
    ndf = next(l for l in parsed.legs if l.ccy == "BRL")
    assert ndf.settles_cash == 0
    assert parsed.instruments["USDBRL"].is_ndf == 1 and parsed.instruments["USDTRY"].is_ndf == 0


@needs_raw
def test_real_future_and_cash(parsed):
    es = parsed.instruments["ESU6 Index"]
    assert (es.asset_class, es.base_ccy, es.quote_ccy, es.multiplier, es.expiry_date) == \
        ("FUTURE", "ES", "USD", 50.0, "2026-09-18")
    # C1: the futures row is a position only, never a trade
    assert not any(t.product == "FUTURE" for t in parsed.trades)
    assert not any(l.leg_type == "NOTIONAL" for l in parsed.legs)
    pos = next(p for p in parsed.positions if p.instrument_id == "ESU6 Index")
    assert (pos.account, pos.settle_date, pos.quantity, pos.mark, pos.fx_to_usd) == \
        ("GSIL-FUT-NMMF", "2026-09-18", 27.0, 7768.75, 1.0)
    assert pos.cost_local == 10234475.0 and pos.mv_usd == 253337.5
    cash = [p for p in parsed.positions if p.instrument_id.startswith("CASH-")]
    assert len(cash) == 9
    assert all(p.settle_date == parsed.as_of_date for p in cash)
    assert parsed.instruments["CASH-USD"].asset_class == "CASH"
    assert parsed.instruments["CASH-USD"].instrument_id == "CASH-USD"


@needs_raw
def test_real_load_counts(parsed, raw_df):
    conn = schema.connect()
    bnp.load(RAW, conn)  # strict: the reference file must load clean
    assert conn.execute("SELECT COUNT(*) FROM trades WHERE product='FX_FWD'").fetchone()[0] == 229
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 229
    assert conn.execute("SELECT COUNT(*) FROM trade_legs").fetchone()[0] == 458
    nm = raw_df[(raw_df["Fund"] == "NMMF") & raw_df["Financial Type"].isin(["FORWARD", "CURRENCY", "FUTURES"])]
    assert len(nm) == 239
    # positions grain is the PK (account, instrument, settle_date): forwards on the same
    # pair / value date / account are netted, so the row count is the number of distinct keys.
    expected_keys = len(parsed.positions)
    assert conn.execute("SELECT COUNT(*) FROM positions WHERE source='BNP'").fetchone()[0] == expected_keys
    assert expected_keys == 31
    assert conn.execute("SELECT COUNT(*) FROM positions WHERE instrument_id='ESU6 Index'").fetchone()[0] == 1


@needs_raw
def test_real_positions_mv_sum_matches_file(parsed, raw_df):
    conn = schema.connect()
    bnp.load(RAW, conn)
    nm = raw_df[(raw_df["Fund"] == "NMMF") & raw_df["Financial Type"].isin(["FORWARD", "CURRENCY", "FUTURES"])]
    db_sum = conn.execute("SELECT SUM(mv_usd) FROM positions WHERE source='BNP'").fetchone()[0]
    assert db_sum == pytest.approx(nm["Market Value Base"].sum(), abs=0.01)
    db_q = conn.execute("SELECT SUM(quantity) FROM positions WHERE source='BNP'").fetchone()[0]
    assert db_q == pytest.approx(nm["Quantity"].sum(), abs=0.01)
    db_dtd = conn.execute("SELECT SUM(pnl_dtd_usd) FROM positions WHERE source='BNP'").fetchone()[0]
    assert db_dtd == pytest.approx(nm["DTD Total P&L"].sum(), abs=0.01)


# --------------------------------------------------------------------------- bnp.load(on_duplicate='skip')
def test_load_on_duplicate_skip_is_idempotent(tmp_path):
    p = _write_csv(tmp_path / "HA_PNL_20260818.csv", [_fwd_row(), _fut_row(), _cash_row()])
    conn = schema.connect()
    r1 = bnp.load(p, conn, on_duplicate="skip")
    assert r1.skipped == {"trades": 0, "legs": 0, "positions": 0}
    assert r1.conflicts == {"trades": 0, "legs": 0, "positions": 0}
    n_trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    n_legs = conn.execute("SELECT COUNT(*) FROM trade_legs").fetchone()[0]
    n_pos = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    assert n_trades == 1 and n_legs == 2 and n_pos == 3

    r2 = bnp.load(p, conn, on_duplicate="skip")
    assert r2.skipped == {"trades": 1, "legs": 2, "positions": 3}
    assert r2.conflicts == {"trades": 0, "legs": 0, "positions": 0}
    assert r2.conflict_details == []
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == n_trades
    assert conn.execute("SELECT COUNT(*) FROM trade_legs").fetchone()[0] == n_legs
    assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == n_pos


def test_load_on_duplicate_skip_detects_conflict_and_keeps_existing_row(tmp_path):
    """Same trade_id, second file has an amended Local Cost / rate -> conflict, not a
    silent skip; the original DB row is kept."""
    p1 = _write_csv(tmp_path / "HA_PNL_20260818.csv", [_fwd_row()])
    conn = schema.connect()
    r1 = bnp.load(p1, conn, on_duplicate="skip")
    assert r1.conflicts == {"trades": 0, "legs": 0, "positions": 0}
    orig_price = conn.execute("SELECT price FROM trades WHERE trade_id='111'").fetchone()[0]

    p2 = _write_csv(tmp_path / "HA_PNL_20260819.csv", [
        _fwd_row(**{"Symbol Description": "TD 08/03/2026 VD 09/16/2026 BUY USD VS .SELL JPY @ 148.00000000",
                    "Local Cost": 148000000.0, "Market Value Local": -500000.0,
                    "Market Value Base": -3401.35, "DTD Total P&L": -6401.35, "DTD Trading P&L": -6401.35,
                    "MTD Total P&L": -3401.35})
    ])
    r2 = bnp.load(p2, conn, on_duplicate="skip")
    assert r2.conflicts["trades"] == 1
    assert any("111" in d and "price" in d for d in r2.conflict_details)
    # existing row untouched
    assert conn.execute("SELECT price FROM trades WHERE trade_id='111'").fetchone()[0] == orig_price


def test_load_on_duplicate_default_still_raises(tmp_path):
    """Default behaviour (on_duplicate='error') is unchanged for existing callers."""
    p = _write_csv(tmp_path / "HA_PNL_20260818.csv", [_fwd_row()])
    conn = schema.connect()
    bnp.load(p, conn)
    with pytest.raises(sqlite3.IntegrityError):
        bnp.load(p, conn)


def test_load_on_duplicate_rejects_bad_value(tmp_path):
    p = _write_csv(tmp_path / "HA_PNL_20260818.csv", [_fwd_row()])
    conn = schema.connect()
    with pytest.raises(ValueError, match="on_duplicate"):
        bnp.load(p, conn, on_duplicate="bogus")


# --------------------------------------------------------------------------- data/load.py CLI
@needs_raw
def test_load_cli_real_file_twice_is_idempotent(tmp_path, capsys):
    from data import load as load_mod

    db_path = tmp_path / "risk.db"
    rc1 = load_mod.run([str(RAW), "--db", str(db_path)])
    out1 = capsys.readouterr().out
    assert rc1 == 0
    assert "as_of_date=2026-08-17" in out1
    assert "trades_loaded=229" in out1
    assert "rejects=0" in out1
    assert "skipped=0" in out1
    assert "conflicts=0" in out1

    conn = schema.connect(db_path)
    n_trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    n_legs = conn.execute("SELECT COUNT(*) FROM trade_legs").fetchone()[0]
    n_pos = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    n_marks = conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0]
    assert n_trades == 229 and n_marks > 0
    conn.close()

    rc2 = load_mod.run([str(RAW), "--db", str(db_path)])
    out2 = capsys.readouterr().out
    assert rc2 == 0
    assert "trades_loaded=0" in out2
    assert "rejects=0" in out2
    assert "conflicts=0" in out2
    assert f"skipped={229 + n_marks}" in out2  # skipped includes trades + marks
    assert "skipped=262" in out2

    conn = schema.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == n_trades
    assert conn.execute("SELECT COUNT(*) FROM trade_legs").fetchone()[0] == n_legs
    assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == n_pos
    assert conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0] == n_marks
    conn.close()


def test_load_cli_regex_failing_row_exits_nonzero_and_zero_with_allow_rejects(tmp_path, capsys):
    from data import load as load_mod

    p = _write_csv(tmp_path / "HA_PNL_20260818.csv", [
        _fwd_row(),
        _fwd_row(Symbol="USDJPY091626-112",
                 **{"Symbol Description": "TD 08/03/2026 VD 09/16/2026 BUY USD VS SELL JPY @ 147.00000000"}),
    ])
    db_path = tmp_path / "risk.db"
    rc = load_mod.run([str(p), "--db", str(db_path)])
    out = capsys.readouterr().out
    assert rc != 0
    # the bad row is rejected both by the trade parser and by the BNP_BVAL mark extractor
    assert "rejects=2" in out
    assert "trades_loaded=1" in out

    db_path2 = tmp_path / "risk2.db"
    rc_allowed = load_mod.run([str(p), "--db", str(db_path2), "--allow-rejects"])
    out2 = capsys.readouterr().out
    assert rc_allowed == 0
    assert "rejects=2" in out2


def test_load_cli_amended_trade_is_a_conflict_not_a_silent_skip(tmp_path, capsys):
    """Same trade_id re-appears in a later file with a different fill rate: this must
    surface as conflicts=3 (trades, legs, positions) with a non-zero exit, and the original DB row must survive
    untouched; --allow-conflicts exits 0 without altering the DB."""
    from data import load as load_mod

    p1 = _write_csv(tmp_path / "HA_PNL_20260818.csv", [_fwd_row()])
    db_path = tmp_path / "risk.db"
    rc1 = load_mod.run([str(p1), "--db", str(db_path)])
    capsys.readouterr()
    assert rc1 == 0

    conn = schema.connect(db_path)
    orig_price = conn.execute("SELECT price FROM trades WHERE trade_id='111'").fetchone()[0]
    conn.close()

    p2 = _write_csv(tmp_path / "HA_PNL_20260819.csv", [
        _fwd_row(**{"Symbol Description": "TD 08/03/2026 VD 09/16/2026 BUY USD VS .SELL JPY @ 148.00000000",
                    "Local Cost": 148000000.0, "Market Value Local": -500000.0,
                    "Market Value Base": -3401.35, "DTD Total P&L": -6401.35, "DTD Trading P&L": -6401.35,
                    "MTD Total P&L": -3401.35})
    ])
    rc2 = load_mod.run([str(p2), "--db", str(db_path)])
    out2 = capsys.readouterr().out
    assert rc2 != 0
    assert "conflicts=3" in out2
    assert "rejects=0" in out2

    conn = schema.connect(db_path)
    assert conn.execute("SELECT price FROM trades WHERE trade_id='111'").fetchone()[0] == orig_price
    conn.close()

    db_path3 = tmp_path / "risk3.db"
    rc3a = load_mod.run([str(p1), "--db", str(db_path3)])
    capsys.readouterr()
    assert rc3a == 0
    rc3b = load_mod.run([str(p2), "--db", str(db_path3), "--allow-conflicts"])
    out3b = capsys.readouterr().out
    assert rc3b == 0
    assert "conflicts=3" in out3b
    conn = schema.connect(db_path3)
    assert conn.execute("SELECT price FROM trades WHERE trade_id='111'").fetchone()[0] == orig_price
    conn.close()


def test_load_cli_amended_mark_value_is_a_conflict(tmp_path, capsys):
    """Same key, different mark value (BNP re-issues a corrected forward-outright Price
    for an existing trade_id): conflict, not a silent skip; existing mark row kept."""
    from data import load as load_mod

    p1 = _write_csv(tmp_path / "HA_PNL_20260818.csv", [_fwd_row()])
    db_path = tmp_path / "risk.db"
    assert load_mod.run([str(p1), "--db", str(db_path)]) == 0
    capsys.readouterr()

    conn = schema.connect(db_path)
    orig_value = conn.execute(
        "SELECT value FROM marks WHERE instrument_id='USDJPY' AND mark_type='FWD_OUTRIGHT'"
    ).fetchone()[0]
    conn.close()

    # Re-issued file for the SAME as_of_date (BNP corrects a prior day's snapshot), so
    # the mark's primary key collides rather than landing on a new as_of_date.
    p2 = _write_csv(tmp_path / "HA_PNL_20260819.csv", [
        _fwd_row(Price=149.0, **{"Market Value Local": 2000000.0, "Market Value Base": 13605.40,
                                  "DTD Total P&L": 10605.40, "DTD Trading P&L": 10605.40,
                                  "MTD Total P&L": 13605.40})
    ])
    rc2 = load_mod.run([str(p2), "--db", str(db_path), "--as-of", "2026-08-17"])
    out2 = capsys.readouterr().out
    assert rc2 != 0
    assert "rejects=0" in out2
    assert "conflicts=2"  # marks value + positions.mark in out2

    conn = schema.connect(db_path)
    kept = conn.execute(
        "SELECT value FROM marks WHERE instrument_id='USDJPY' AND mark_type='FWD_OUTRIGHT'"
    ).fetchone()[0]
    assert kept == orig_value
    conn.close()


def test_load_cli_local_cost_only_amendment_is_a_conflict(tmp_path, capsys):
    """Amended Local Cost on an existing trade changes only trade_legs.amount and
    positions.cost_local (trades.price is the fill rate, unchanged). The headline
    conflicts count must include those tables and the exit must be non-zero; DB unchanged.
    |Q x rate - Local Cost| = 0.5 keeps the recon check (<= 1) passing."""
    from data import load as load_mod

    p1 = _write_csv(tmp_path / "HA_PNL_20260818.csv", [_fwd_row()])
    db_path = tmp_path / "risk.db"
    assert load_mod.run([str(p1), "--db", str(db_path)]) == 0
    capsys.readouterr()

    conn = schema.connect(db_path)
    orig_leg = conn.execute(
        "SELECT amount FROM trade_legs WHERE trade_id='111' AND ccy='JPY'").fetchone()[0]
    orig_cost = conn.execute(
        "SELECT cost_local FROM positions WHERE instrument_id='USDJPY'").fetchone()[0]
    conn.close()
    assert orig_leg == -147000000.0 and orig_cost == 147000000.0

    p2 = _write_csv(tmp_path / "HA_PNL_20260819.csv",
                    [_fwd_row(**{"Local Cost": 147000000.5})])
    rc2 = load_mod.run([str(p2), "--db", str(db_path), "--as-of", "2026-08-17"])
    out2 = capsys.readouterr().out
    assert rc2 != 0
    assert "rejects=0" in out2
    assert "conflicts=2" in out2  # trade_legs.amount (JPY leg) + positions.cost_local

    conn = schema.connect(db_path)
    assert conn.execute(
        "SELECT amount FROM trade_legs WHERE trade_id='111' AND ccy='JPY'").fetchone()[0] == orig_leg
    assert conn.execute(
        "SELECT cost_local FROM positions WHERE instrument_id='USDJPY'").fetchone()[0] == orig_cost
    conn.close()


def test_load_cli_currency_balance_amendment_is_a_conflict(tmp_path, capsys):
    """A CURRENCY row creates no trades row, only a positions row. An amended cash
    balance must still surface as conflicts=1 with a non-zero exit; DB unchanged."""
    from data import load as load_mod

    p1 = _write_csv(tmp_path / "HA_PNL_20260818.csv", [_cash_row()])
    db_path = tmp_path / "risk.db"
    assert load_mod.run([str(p1), "--db", str(db_path)]) == 0
    capsys.readouterr()

    conn = schema.connect(db_path)
    orig_qty = conn.execute(
        "SELECT quantity FROM positions WHERE instrument_id='CASH-TRY'").fetchone()[0]
    conn.close()
    assert orig_qty == 1000.0

    p2 = _write_csv(tmp_path / "HA_PNL_20260819.csv", [
        _cash_row(Quantity=2000.0, Position=2000.0,
                  **{"Market Value Local": 2000.0, "Market Value Base": 40.0})
    ])
    rc2 = load_mod.run([str(p2), "--db", str(db_path), "--as-of", "2026-08-17"])
    out2 = capsys.readouterr().out
    assert rc2 != 0
    assert "rejects=0" in out2
    assert "conflicts=1" in out2

    conn = schema.connect(db_path)
    assert conn.execute(
        "SELECT quantity FROM positions WHERE instrument_id='CASH-TRY'").fetchone()[0] == orig_qty
    conn.close()
