"""engine/risk: the Risk tab's metrics (the nm-dashboard's definitions on the book's own
positions), on synthetic parquet history written to tmp_path; never on the sibling repo.
The macro trader's rates (DV01) and equity-index (ES + SPX) underlyers left in Phase 2
(user approval 2026-09-24): `book_positions`' `rates` and `equity_index` blocks are not read."""
import json
import math
import os
import sqlite3

import numpy as np
import pandas as pd
import pytest

from data.contracts import get_root
from data.ingest import schema
from engine.pnl import stress
from engine.risk import book_risk, load_config, load_history
from engine.risk import commodity_history as ch_mod
from engine.risk import history as history_mod
from engine.risk.commodity_history import load_commodity_history
from engine.risk.config import DEFAULTS
from engine.risk.history import History
from engine.risk.metrics import CRISIS_FALLBACK, DELTA_KINDS, KIND_FX, KIND_METAL, rows_from_positions, unit_moves

AS_OF = "2026-09-22"
SNAP = f"{AS_OF}T15:00:00-04:00"
DATES = pd.bdate_range("2007-01-01", AS_OF)        # ends on as_of: lag-2 = 2026-09-18
BASE = 0.0005                                        # the everyday log return of every synthetic series
SNB, CRISIS_DAY, WORST_EX_DAY, LAST = "2015-01-15", "2009-03-02", "2020-03-16", AS_OF
VAR_DIP_OFFSETS = tuple(range(20, 170, 10))         # 15 days of -1 % inside the last 252 observations
USD_YIELD, MXN_YIELD = 3.0, 10.0

# the book: currency -> (quoted USD pair rate, local delta) giving the USD delta on the right
CHF_USD, JPY_USD, XAU_USD, MXN_USD, SEK_USD, PLN_USD, CZK_USD = \
    1_250_000.0, -1_000_000.0, 400_000.0, 500_000.0, 300_000.0, 250_000.0, 80_000.0
ROWS = ["CHF", "JPY", "MXN", "XAU", "SEK", "PLN", "CZK", "NOK"]     # by gross USD, NOK (no rate) last


def _returns(special: dict) -> np.ndarray:
    """Log returns on DATES: BASE everywhere, `special` {date: return} overriding."""
    r = pd.Series(BASE, index=DATES)
    for date, value in special.items():
        r.loc[pd.Timestamp(date)] = value
    return r.to_numpy(copy=True)


def _price(returns: np.ndarray) -> np.ndarray:
    return np.exp(np.cumsum(returns))


def chf_returns():
    special = {SNB: -0.15, CRISIS_DAY: -0.02, WORST_EX_DAY: -0.03, LAST: -0.04}
    r = _returns(special)
    for k in VAR_DIP_OFFSETS:
        r[len(DATES) - k] = -0.01
    return r


def write_history(folder, *, yields=True, end=None):
    """The synthetic history: spot (USD per unit) and yields (percent); `end` truncates
    every file to that date (a stale copy). The SPX column is the nm-dashboard's own, kept
    to show that a column with no underlyer in the book is loaded and never asked for."""
    folder.mkdir(parents=True, exist_ok=True)
    spot = pd.DataFrame(index=DATES)
    spot.index.name = "date"
    spot["CHF"] = _price(chf_returns())
    spot["JPY"] = 0.0067 * _price(_returns({WORST_EX_DAY: 0.03, "2008-10-24": 0.02, "2024-08-05": -0.05}))
    spot["XAU"] = 2000.0 * _price(_returns({}))
    spot["SPX"] = 5000.0 * _price(_returns({}))
    spot["MXN"] = np.full(len(DATES), 0.05)                          # never moves: carry alone
    spot["PLN"] = np.where(np.arange(len(DATES)) >= len(DATES) - 300, 0.25 * _price(_returns({})), np.nan)
    spot["CZK"] = np.where(np.arange(len(DATES)) >= len(DATES) - 100, 0.04 * _price(_returns({})), np.nan)
    spot.loc[:end].to_parquet(folder / history_mod.SPOT_FILE)
    if yields:
        y = pd.DataFrame(index=DATES)
        y.index.name = "date"
        y["USD"] = USD_YIELD
        y["CHF"] = USD_YIELD
        y["JPY"] = USD_YIELD
        y["XAU"] = 0.0
        y["SPX"] = USD_YIELD
        y["MXN"] = MXN_YIELD
        y.loc[pd.Timestamp(LAST), "MXN"] = 20.0          # a jump on the last day: the carry of that day is the lagged rate
        y["CZK"] = USD_YIELD                              # PLN deliberately absent
        y.loc[:end].to_parquet(folder / history_mod.YIELDS_FILE)
    return folder


@pytest.fixture
def history(tmp_path):
    return load_history(write_history(tmp_path / "hist"))


def _book(*, settle="2026-10-20"):
    conn = schema.connect()
    conn.executemany("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                     "bbg_ticker, expiry_date) VALUES (?,?,?,?,?,?,?,?)", [
        ("USDCHF", "FX", "USD", "CHF", 1, 0, "USDCHF Curncy", "9999-12-31"),
        ("USDJPY", "FX", "USD", "JPY", 1, 0, "USDJPY Curncy", "9999-12-31"),
        ("XAUUSD", "FX", "XAU", "USD", 1, 0, "XAUUSD Curncy", "9999-12-31"),
        ("USDMXN", "FX", "USD", "MXN", 1, 0, "USDMXN Curncy", "9999-12-31"),
        ("USDSEK", "FX", "USD", "SEK", 1, 0, "USDSEK Curncy", "9999-12-31"),
        ("USDNOK", "FX", "USD", "NOK", 1, 0, "USDNOK Curncy", "9999-12-31"),
        ("USDPLN", "FX", "USD", "PLN", 1, 0, "USDPLN Curncy", "9999-12-31"),
        ("USDCZK", "FX", "USD", "CZK", 1, 0, "USDCZK Curncy", "9999-12-31"),
    ])
    fx = [  # trade, pair, base leg ccy/amount, quote leg ccy/amount, fill; the USD deltas come from the SPOT marks below
        ("c1", "USDCHF", ("USD", -1_000_000.0), ("CHF", 800_000.0), 0.80),      # long 0.8m CHF, marked at USDCHF 0.64 = $1.25m
        ("j1", "USDJPY", ("USD", 1_000_000.0), ("JPY", -150_000_000.0), 150.0),
        ("g1", "XAUUSD", ("XAU", 100.0), ("USD", -400_000.0), 4000.0),
        ("m1", "USDMXN", ("USD", -500_000.0), ("MXN", 10_000_000.0), 20.0),
        ("s1", "USDSEK", ("USD", -300_000.0), ("SEK", 3_000_000.0), 10.0),
        ("n1", "USDNOK", ("USD", -100_000.0), ("NOK", 1_000_000.0), 10.0),
        ("p1", "USDPLN", ("USD", -250_000.0), ("PLN", 1_000_000.0), 4.0),
        ("z1", "USDCZK", ("USD", -80_000.0), ("CZK", 2_000_000.0), 25.0),
    ]
    for tid, pair, (bccy, bamt), (qccy, qamt), px in fx:
        conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (tid, "XLSX", pair, "FX_FWD", tid, "2026-09-01", bamt, px, "acc", "cp", "", "t", "d", ""))
        conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
            (tid, 1, "FX_NEAR", bccy, bamt, "2026-09-01", settle, px, 1),
            (tid, 2, "FX_NEAR", qccy, qamt, "2026-09-01", settle, px, 1)])
    marks = [("USDCHF", AS_OF, "SPOT", 0.64), ("USDJPY", AS_OF, "SPOT", 150.0), ("XAUUSD", AS_OF, "SPOT", 4000.0),
             ("USDMXN", AS_OF, "SPOT", 20.0), ("USDSEK", AS_OF, "SPOT", 10.0), ("USDPLN", AS_OF, "SPOT", 4.0),
             ("USDCZK", AS_OF, "SPOT", 25.0)]          # NOK: no rate on purpose
    for inst, settle_d, mt, value in marks:
        conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", (AS_OF, inst, settle_d, mt, value, "BBG_BFXFORWARD", SNAP))
    conn.commit()
    return conn


def _rows(out):
    return {r["underlyer"]: r for r in out["underlyers"]}


def _pnl_by_hand(returns: np.ndarray, exposure: float, carry_per_day: float = 0.0) -> pd.Series:
    """exposure x (log return + carry) on DATES[1:], the way the metrics define a row's daily P&L."""
    return pd.Series(exposure * (returns[1:] + carry_per_day), index=DATES[1:])


# --------------------------------------------------------------------------- history
def test_history_dir_env_var_is_the_only_candidate_and_a_missing_one_says_so(tmp_path, monkeypatch):
    folder = write_history(tmp_path / "h")
    fresher = write_history(tmp_path / "fresher-but-not-asked")
    monkeypatch.setattr(history_mod, "DEFAULT_DIRS", (fresher,))
    monkeypatch.setenv(history_mod.ENV_VAR, str(folder))
    chosen, seen, note = history_mod.history_dir()
    assert chosen == folder and seen == [{"path": str(folder), "exists": True, "last_date": AS_OF}]
    assert note == f"RISK_HISTORY_DIR = {folder} (to {AS_OF})"
    h = load_history()
    assert h.available and h.path == str(folder) and h.last_date == AS_OF and h.note == note and h.candidates == seen
    monkeypatch.setenv(history_mod.ENV_VAR, str(tmp_path / "nowhere"))
    h = load_history()
    assert not h.available and str(tmp_path / "nowhere") in h.reason and "no market history folder" in h.reason
    assert h.candidates == [{"path": str(tmp_path / "nowhere"), "exists": False, "last_date": None}]


def test_history_uses_the_only_existing_default_folder_and_names_the_missing_ones(tmp_path, monkeypatch):
    monkeypatch.delenv(history_mod.ENV_VAR, raising=False)
    folder = write_history(tmp_path / "sibling" / "bbg_data")
    monkeypatch.setattr(history_mod, "DEFAULT_DIRS", (tmp_path / "missing-1", folder, tmp_path / "missing-2"))
    chosen, seen, note = history_mod.history_dir()
    assert chosen == folder and note == f"1 copy found, using {folder} (to {AS_OF})"
    assert [(c["exists"], c["last_date"]) for c in seen] == [(False, None), (True, AS_OF), (False, None)]
    monkeypatch.setattr(history_mod, "DEFAULT_DIRS", (tmp_path / "missing-1", tmp_path / "missing-2"))
    h = load_history()
    assert not h.available and str(tmp_path / "missing-1") in h.reason and str(tmp_path / "missing-2") in h.reason


def test_the_freshest_copy_wins_whatever_its_position_and_the_note_lists_every_copy(tmp_path, monkeypatch):
    monkeypatch.delenv(history_mod.ENV_VAR, raising=False)
    stale = write_history(tmp_path / "nm-dashboard" / "bbg_data", end="2026-08-13")
    fresh = write_history(tmp_path / "bbg_data" / "bbg_data")
    empty = tmp_path / "exists-without-spot"
    empty.mkdir()
    monkeypatch.setattr(history_mod, "DEFAULT_DIRS", (stale, empty, fresh))
    chosen, seen, note = history_mod.history_dir()
    assert chosen == fresh                                   # later in the order, but it runs to 2026-09-22
    assert note == (f"3 copies found, using {fresh} (to {AS_OF}); {stale} ends 2026-08-13; "
                    f"{empty} has no spot file")
    assert [c["last_date"] for c in seen] == ["2026-08-13", None, AS_OF]
    h = load_history()
    assert h.path == str(fresh) and h.last_date == AS_OF and h.note == note
    out = book_risk(_book(), AS_OF, history=h)
    assert out["history"]["path"] == str(fresh) and out["history"]["note"] == note   # no coverage gap to add
    assert out["history"]["candidates"] == seen
    # a tie keeps the order: two copies to the same date, the first one wins
    twin = write_history(tmp_path / "twin")
    monkeypatch.setattr(history_mod, "DEFAULT_DIRS", (twin, fresh))
    assert history_mod.history_dir()[0] == twin
    # the stale copy alone, with the book on a later day: the copies note comes first, then the coverage
    monkeypatch.setattr(history_mod, "DEFAULT_DIRS", (stale,))
    out = book_risk(_book(), AS_OF, history=load_history())
    assert out["history"]["note"] == (f"1 copy found, using {stale} (to 2026-08-13); history ends 2026-08-13, "
                                      f"before {AS_OF}: metrics on the history to 2026-08-13 "
                                      f"(the book's positions are {AS_OF}'s)")
    assert out["history"]["lag2_date"] == "2026-08-11"


def test_history_loads_the_files_names_a_missing_one_and_caches_by_mtime(tmp_path):
    folder = write_history(tmp_path / "h")
    (folder / "bbg_raw_rates.parquet").write_bytes(b"not read")   # the retired swap-rate file: never opened
    h = load_history(folder)
    assert h.available and h.files["spot"]["loaded"] and h.files["yields"]["loaded"]
    assert set(h.files) == {"spot", "yields"}
    assert list(h.spot.columns) == ["CHF", "JPY", "XAU", "SPX", "MXN", "PLN", "CZK"] and h.spot.index.name == "date"
    assert h.files["spot"]["first_date"] == "2007-01-01" and h.files["spot"]["last_date"] == AS_OF
    assert load_history(folder) is h                      # cached
    assert history_mod.spot_last_date(folder) == AS_OF    # the index alone, memoised by mtime
    spot_file = folder / history_mod.SPOT_FILE
    os.utime(spot_file, (os.path.getmtime(spot_file) + 10, os.path.getmtime(spot_file) + 10))
    assert load_history(folder) is not h                  # a new pull invalidates it
    # no spot file at all: not available, the path named
    (folder / history_mod.SPOT_FILE).unlink()
    h2 = load_history(folder)
    assert not h2.available and str(spot_file) in h2.reason


# --------------------------------------------------------------------------- config
def test_config_defaults_are_the_dashboards_constants_and_a_file_overrides_them(tmp_path):
    cfg = load_config(tmp_path / "absent.yaml")
    assert not cfg["loaded"] and "defaults in use" in cfg["note"]
    assert cfg["blended"] == {"trail_window_bd": 500, "w_trail": 2 / 3, "w_stress": 1 / 3, "stress_start": "2008-01-01",
                              "stress_end": "2010-12-31", "cutover": "2011-01-01"}
    assert (cfg["vol_target_usd"], cfg["stress_pct"], cfg["var_window_bd"], cfg["var_confidence"]) == (4.5e6, 50.0, 252, 0.95)
    assert cfg["worst_day_start"] == "2008-01-01"
    # the dashboard's two, then the commodity one-offs of the research history (Phase 4)
    assert [d["date"] for d in cfg["shock_dates"]] == ["2015-01-15", "2016-06-24", "2020-04-20", "2020-04-21",
                                                       "2022-03-07", "2022-03-08"]
    p = tmp_path / "risk.yaml"
    p.write_text("vol_target_usd: 6.0e7\nstress_pct: 35\nblended: {trail_window_bd: 100, cutover: 2099-01-01}\n"
                 "shock_dates:\n  - {date: 2020-03-16, name: covid}\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg["loaded"] and cfg["vol_target_usd"] == 6.0e7 and cfg["stress_pct"] == 35.0
    assert cfg["blended"]["trail_window_bd"] == 100 and cfg["blended"]["cutover"] == "2099-01-01"
    assert cfg["blended"]["w_trail"] == 2 / 3 and cfg["shock_dates"] == [{"date": "2020-03-16", "name": "covid"}]
    # the shipped file agrees with the defaults on every number
    shipped = load_config()
    assert shipped["loaded"]
    for key in ("vol_target_usd", "vol_target_placeholder", "vol_target_note", "stress_pct", "var_window_bd",
                "var_confidence", "worst_day_start", "shock_dates"):
        assert shipped[key] == DEFAULTS[key], key
    for key, value in DEFAULTS["blended"].items():
        assert shipped["blended"][key] == pytest.approx(value), key


# --------------------------------------------------------------------------- rows
def test_positions_become_rows_with_the_right_kinds_sizes_and_order(history):
    out = book_risk(_book(), AS_OF, history=history)
    rows = _rows(out)
    assert [r["underlyer"] for r in out["underlyers"]] == ROWS
    assert (rows["CHF"]["kind"], rows["XAU"]["kind"]) == (KIND_FX, KIND_METAL)
    assert {r["kind"] for r in out["underlyers"]} <= set(DELTA_KINDS)
    assert rows["CHF"]["net_usd"] == pytest.approx(CHF_USD) and rows["CHF"]["gross_usd"] == pytest.approx(CHF_USD)
    assert rows["JPY"]["net_usd"] == pytest.approx(JPY_USD) and rows["JPY"]["gross_usd"] == pytest.approx(-JPY_USD)
    assert all("dv01_usd" not in r for r in out["underlyers"])
    book = out["book"]
    assert book["net_usd"] == pytest.approx(CHF_USD + JPY_USD + XAU_USD + MXN_USD + SEK_USD + PLN_USD + CZK_USD)
    assert book["gross_usd"] == pytest.approx(CHF_USD - JPY_USD + XAU_USD + MXN_USD + SEK_USD + PLN_USD + CZK_USD)
    assert "dv01_usd" not in book
    # the header's FX net is the positions module's own: NaN while NOK has no rate, named in `missing`
    assert math.isnan(book["fx_net_usd"]) and any(m.startswith("FX positions: ") and "NOK" in m for m in out["missing"])
    assert out["config"]["stress_cap_usd"] == 2_250_000.0 and out["as_of"] == AS_OF
    conn = _book()
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", (AS_OF, "USDNOK", AS_OF, "SPOT", 10.0, "BBG_BFXFORWARD", SNAP))
    conn.commit()
    book = book_risk(conn, AS_OF, history=history)["book"]
    assert book["fx_net_usd"] == pytest.approx(-(CHF_USD + JPY_USD + MXN_USD + SEK_USD + PLN_USD + CZK_USD + 100_000.0))  # + = long USD
    assert book["fx_gross_usd"] == pytest.approx(CHF_USD - JPY_USD + MXN_USD + SEK_USD + PLN_USD + CZK_USD + 100_000.0)


def test_long_chf_loses_when_chf_falls_and_short_jpy_loses_when_jpy_rises(history):
    rows = _rows(book_risk(_book(), AS_OF, history=history))
    chf, jpy = rows["CHF"], rows["JPY"]
    assert chf["worst_1d_raw_usd"] == pytest.approx(CHF_USD * -0.15) and chf["worst_1d_raw_date"] == SNB
    assert jpy["worst_1d_raw_usd"] == pytest.approx(JPY_USD * 0.03) and jpy["worst_1d_raw_date"] == WORST_EX_DAY
    assert chf["carry"] and jpy["carry"] and chf["reason"] == "" and chf["reasons"] == {}


def test_the_retired_equity_index_and_rates_blocks_are_not_read():
    """`book_positions` may still carry the ES + SPX line and the DV01 lines while the lanes
    below remove them: no row, no `missing` entry and nothing in the book comes from them."""
    positions = {
        "fx": {"available": True, "by_ccy": [{"ccy": "CHF", "usd_delta": CHF_USD},
                                              {"ccy": "XAU", "usd_delta": XAU_USD, "metal": True},
                                              {"ccy": "USD", "usd_delta": -CHF_USD}]},
        "equity_index": {"lines": [{"instrument_id": "ESZ6 Index"}], "usd_delta": 700_000.0,
                         "missing": ["SPX option o1: no DELTA"], "reason": "one option unpriced"},
        "rates": {"by_ccy": {"USD": 4200.0}, "missing": ["IRS r2: no DV01_USD"], "reason": "one swap unpriced"},
        "fx_options": {},
    }
    rows, missing = rows_from_positions(positions)
    assert [(r["underlyer"], r["kind"]) for r in rows] == [("CHF", KIND_FX), ("XAU", KIND_METAL)]
    assert missing == []
    assert all(set(r) == {"underlyer", "kind", "series", "net_usd", "gross_usd", "carry", "reason", "note"} for r in rows)


def test_blended_vol_is_two_thirds_trailing_plus_one_third_crisis_on_the_lag2_series(history):
    chf = _rows(book_risk(_book(), AS_OF, history=history))["CHF"]
    pnl = _pnl_by_hand(chf_returns(), CHF_USD)             # CHF yield = USD yield: no carry
    s_hist = pnl.loc[:"2026-09-18"]                        # last date 2026-09-22 less 2 business days
    trailing = s_hist.iloc[-500:].std() * math.sqrt(252)
    crisis = s_hist.loc["2008-01-01":"2010-12-31"].std() * math.sqrt(252)
    assert crisis != pytest.approx(trailing)
    assert chf["vol_trailing_ann_usd"] == pytest.approx(trailing, rel=1e-9)
    assert chf["vol_crisis_ann_usd"] == pytest.approx(crisis, rel=1e-9)
    assert chf["vol_blended_ann_usd"] == pytest.approx(2 / 3 * trailing + 1 / 3 * crisis, rel=1e-9)
    assert chf["days"] == len(DATES) - 1 and chf["first_date"] == "2007-01-02" and chf["last_date"] == AS_OF


def test_before_the_cutover_the_crisis_leg_is_the_trailing_vol(history):
    cfg = load_config(history.path + "/absent.yaml")
    cfg["blended"]["cutover"] = "2099-01-01"
    chf = _rows(book_risk(_book(), AS_OF, history=history, config=cfg))["CHF"]
    assert chf["vol_crisis_ann_usd"] == chf["vol_trailing_ann_usd"]
    assert chf["vol_blended_ann_usd"] == pytest.approx(chf["vol_trailing_ann_usd"])


def test_vol_needs_500_observations_to_the_lag2_date_and_var_needs_252(history):
    rows = _rows(book_risk(_book(), AS_OF, history=history))
    pln = rows["PLN"]                                        # 300 closes: 299 moves, 297 to the lag-2 date
    assert math.isnan(pln["vol_blended_ann_usd"]) and pln["reasons"]["vol_blended_ann_usd"] == \
        "needs 500 daily observations to 2026-09-18: 297 on file"
    assert not math.isnan(pln["var95_1d_usd"]) and pln["days"] == 299
    czk = rows["CZK"]                                        # 100 closes: 99 moves
    assert math.isnan(czk["var95_1d_usd"]) and czk["reasons"]["var95_1d_usd"] == "needs 252 daily observations: 99 on file"
    assert not math.isnan(czk["worst_1d_raw_usd"])
    cfg = load_config(history.path + "/absent.yaml")
    cfg["blended"]["trail_window_bd"] = 100
    assert not math.isnan(_rows(book_risk(_book(), AS_OF, history=history, config=cfg))["PLN"]["vol_blended_ann_usd"])


def test_var95_is_minus_the_5th_percentile_of_the_last_252_days_of_the_full_series(history):
    chf = _rows(book_risk(_book(), AS_OF, history=history))["CHF"]
    # the last 252 daily P&Ls: one of -4 % (the last day, inside VaR's window though past the lag-2 cut),
    # fifteen of -1 %, the rest +0.05 %: the 5th percentile (position 12.55 of 251) sits on a -1 % day
    assert chf["var95_1d_usd"] == pytest.approx(0.01 * CHF_USD)
    by_hand = -np.percentile(_pnl_by_hand(chf_returns(), CHF_USD).iloc[-252:].to_numpy(), 5)
    assert chf["var95_1d_usd"] == pytest.approx(by_hand)


def test_worst_day_ex_shocks_zeroes_the_shock_dates_and_stops_at_the_lag2_date(history):
    out = book_risk(_book(), AS_OF, history=history)
    chf = _rows(out)["CHF"]
    assert out["history"]["lag2_date"] == "2026-09-18" and out["history"]["used_to"] == AS_OF
    # raw: the SNB day; ex shocks: the -3 % day, not the SNB day (zeroed) and not the last day's -4 % (past lag-2)
    assert (chf["worst_1d_raw_usd"], chf["worst_1d_raw_date"]) == (pytest.approx(CHF_USD * -0.15), SNB)
    assert (chf["worst_1d_ex_shocks_usd"], chf["worst_1d_ex_shocks_date"]) == (pytest.approx(CHF_USD * -0.03), WORST_EX_DAY)
    assert chf["worst_day_ex_vs_target_pct"] == pytest.approx(0.03 * CHF_USD / 4.5e6 * 100)
    cfg = load_config(history.path + "/absent.yaml")
    cfg["shock_dates"] = []
    chf = _rows(book_risk(_book(), AS_OF, history=history, config=cfg))["CHF"]
    assert (chf["worst_1d_ex_shocks_usd"], chf["worst_1d_ex_shocks_date"]) == (pytest.approx(CHF_USD * -0.15), SNB)
    cfg["worst_day_start"] = "2016-01-01"                    # the window starts after both big days
    chf = _rows(book_risk(_book(), AS_OF, history=history, config=cfg))["CHF"]
    assert chf["worst_1d_ex_shocks_date"] == WORST_EX_DAY


def test_carry_is_the_lagged_yield_differential_per_calendar_day(history, tmp_path):
    mxn = _rows(book_risk(_book(), AS_OF, history=history))["MXN"]
    carry = (MXN_YIELD - USD_YIELD) / 100 / 365
    assert mxn["carry"] and mxn["worst_1d_raw_usd"] == pytest.approx(MXN_USD * carry)   # the spot never moves
    assert mxn["var95_1d_usd"] == pytest.approx(-MXN_USD * carry)
    moves, note, reason = unit_moves(history, KIND_FX, "MXN", AS_OF)
    assert (note, reason) == ("", "")
    assert moves.loc[LAST] == pytest.approx(carry)           # the last day's jump to 20 % is not in that day's carry
    assert moves.loc["2007-01-02"] == pytest.approx(carry) and pd.Timestamp("2007-01-01") not in moves.index
    xau = _rows(book_risk(_book(), AS_OF, history=history))["XAU"]
    assert xau["carry"]                                      # gold pays no yield: it funds at minus the USD rate
    pln = _rows(book_risk(_book(), AS_OF, history=history))["PLN"]
    assert not pln["carry"] and "carry not included: no PLN column" in pln["note"]
    # no yields file at all: the spot term alone, said in the row and in the history block
    bare = load_history(write_history(tmp_path / "bare", yields=False))
    out = book_risk(_book(), AS_OF, history=bare)
    mxn = _rows(out)["MXN"]
    assert not mxn["carry"] and "carry not included" in mxn["note"] and mxn["worst_1d_raw_usd"] == 0.0
    assert not out["history"]["carry"] and "carry not included" in out["history"]["note"]


def test_book_series_is_the_sum_of_the_rows_series(history):
    out = book_risk(_book(), AS_OF, history=history)
    book = out["book"]
    assert book["rows_in_series"] == ["CHF", "JPY", "MXN", "XAU", "PLN", "CZK"]
    mxn_c, xau_c = (MXN_YIELD - USD_YIELD) / 36500, (0.0 - USD_YIELD) / 36500
    # the SNB day: CHF's -15 % plus every other row's ordinary day (PLN and CZK have no data yet: left out, not zero)
    snb = CHF_USD * -0.15 + JPY_USD * BASE + XAU_USD * (BASE + xau_c) + MXN_USD * mxn_c
    assert (book["worst_1d_raw_usd"], book["worst_1d_raw_date"]) == (pytest.approx(snb), SNB)
    # the last day: CHF's -4 %, PLN and CZK now in
    last = CHF_USD * -0.04 + JPY_USD * BASE + XAU_USD * (BASE + xau_c) + MXN_USD * mxn_c + \
        PLN_USD * BASE + CZK_USD * BASE
    window = book["var_window"]
    assert window["dates"][-1] == AS_OF and len(window["dates"]) == 252 and window["pnl_usd"][-1] == pytest.approx(last)
    assert book["days"] == len(DATES) - 1
    assert "SEK: not in the book series (no history for SEK)" in out["missing"]
    assert any(m.startswith("NOK: not in the book series (") for m in out["missing"])


def test_book_caps_and_limit_flags(history):
    out = book_risk(_book(), AS_OF, history=history)
    book = out["book"]
    assert book["vol_vs_target_pct"] == pytest.approx(book["vol_blended_ann_usd"] / 4.5e6 * 100)
    assert book["worst_day_ex_vs_cap_pct"] == pytest.approx(-book["worst_1d_ex_shocks_usd"] / 2.25e6 * 100)
    assert book["over_cap"] is False and book["over_vol_target"] is False
    cfg = load_config(history.path + "/absent.yaml")
    cfg["vol_target_usd"] = 20_000.0                        # a tiny target (book vol ~30k): both flags trip
    book = book_risk(_book(), AS_OF, history=history, config=cfg)["book"]
    assert book["over_cap"] is True and book["over_vol_target"] is True and book["worst_day_ex_vs_cap_pct"] > 100


def test_scenarios_are_engine_pnl_stress_passed_through(history):
    out = book_risk(_book(), AS_OF, history=history)
    delta = {"CHF": CHF_USD, "JPY": JPY_USD, "XAU": XAU_USD, "MXN": MXN_USD, "SEK": SEK_USD, "PLN": PLN_USD, "CZK": CZK_USD}
    scenarios = stress.load_scenarios()
    expected = stress.run_scenarios(delta, scenarios)
    assert list(out["scenarios"]) == list(scenarios)
    for name, exp in expected.items():
        got = out["scenarios"][name]
        assert set(got) == {"total", "fx_pnl", "fx_total"}          # no futures line, no equity move
        assert got["fx_pnl"] == pytest.approx(exp["fx_pnl"]) and got["fx_total"] == pytest.approx(exp["fx_total"])
        assert got["total"] == got["fx_total"]
    assert any(m.startswith("NOK: not in the scenarios (") for m in out["missing"])


def test_a_series_not_on_file_and_a_position_without_a_rate_are_nan_with_their_reasons(history):
    rows = _rows(book_risk(_book(), AS_OF, history=history))
    sek = rows["SEK"]
    assert sek["net_usd"] == pytest.approx(SEK_USD) and sek["reason"] == "no history for SEK"
    assert math.isnan(sek["vol_blended_ann_usd"]) and math.isnan(sek["var95_1d_usd"]) and sek["worst_1d_raw_date"] is None
    assert sek["reasons"] == {"all": "no history for SEK"} and sek["days"] == 0
    nok = rows["NOK"]
    assert math.isnan(nok["net_usd"]) and nok["reason"] and math.isnan(nok["worst_1d_raw_usd"])
    assert nok["reasons"] == {"all": nok["reason"]}


def test_history_ending_before_as_of_is_said_and_metrics_still_computed(history):
    out = book_risk(_book(settle="2027-06-30"), "2026-12-31", history=history)
    h = out["history"]
    assert h["available"] and h["last_date"] == AS_OF and h["used_to"] == AS_OF and h["lag2_date"] == "2026-09-18"
    assert h["note"].startswith("history ends 2026-09-22, before 2026-12-31: metrics on the history to 2026-09-22")
    chf = _rows(out)["CHF"]
    assert chf["worst_1d_raw_date"] == SNB and not math.isnan(chf["vol_blended_ann_usd"])
    # a day before the history starts: nothing to compute, every row says so
    out = book_risk(_book(), "2006-06-30", history=load_history(history.path))
    assert "no history on or before 2006-06-30" in out["history"]["note"]
    assert all(math.isnan(r["vol_blended_ann_usd"]) and r["reason"] for r in out["underlyers"])


def test_no_history_folder_keeps_the_positions_and_the_scenarios(tmp_path):
    out = book_risk(_book(), AS_OF, history=load_history(tmp_path / "absent"))
    assert not out["history"]["available"] and "no market history folder" in out["history"]["reason"]
    rows = _rows(out)
    assert rows["CHF"]["net_usd"] == pytest.approx(CHF_USD) and rows["CHF"]["reason"].startswith("no market history")
    assert math.isnan(out["book"]["vol_blended_ann_usd"]) and out["book"]["reason"].startswith("no market history")
    assert out["book"]["net_usd"] == pytest.approx(CHF_USD + JPY_USD + XAU_USD + MXN_USD + SEK_USD + PLN_USD + CZK_USD)
    assert out["scenarios"] and all(s["total"] == s["fx_total"] for s in out["scenarios"].values())


def test_book_risk_writes_nothing(history):
    conn = _book()
    before = conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0]
    changes = conn.total_changes
    out = book_risk(conn, AS_OF, history=history)
    assert conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0] == before and conn.total_changes == changes
    assert conn.execute("SELECT COUNT(*) FROM marks WHERE source != 'BBG_BFXFORWARD'").fetchone()[0] == 0
    assert len(out["underlyers"]) == len(ROWS)


# ======================================================================================
# Commodity underlyers (Phase 4): COMMODITY per root, SECTOR, SPREAD, on a tiny synthetic
# copy of the research app's database written to tmp_path (never the sibling repo).
# ======================================================================================
C_AS_OF = "2026-09-15"
C_DATES = pd.bdate_range("2019-01-02", C_AS_OF)
CLZ6, CLF7, NGX6, CUZ6 = "CLZ26 Comdty", "CLF27 Comdty", "NGX26 Comdty", "CUZ26 Comdty"
WTI_NEG, WTI_AFTER, WORST_CL = "2020-04-20", "2020-04-21", "2021-03-18"
SPREAD_DAY = "2021-06-01"                 # the one day CLF27 does not move with CLZ26
USDCNY_SPOT = 7.1                          # our official spot (curve-positions' USD delta)
MONTH_CODES = "FGHJKMNQUVXZ"

_CM_DDL = """
CREATE TABLE instrument (
    instrument_id TEXT PRIMARY KEY, name TEXT NOT NULL, sector TEXT NOT NULL, subsector TEXT NOT NULL,
    exchange TEXT NOT NULL, country TEXT NOT NULL, exchange_code TEXT NOT NULL, bbg_root TEXT NOT NULL,
    bbg_yellow_key TEXT NOT NULL, bbg_verified INTEGER NOT NULL DEFAULT 0, currency TEXT NOT NULL,
    contract_size REAL NOT NULL, size_unit TEXT NOT NULL, quote_unit TEXT NOT NULL,
    price_scale REAL NOT NULL DEFAULT 1.0, vat_rate REAL NOT NULL DEFAULT 0.0,
    active_months TEXT NOT NULL DEFAULT '', calendar_depth INTEGER NOT NULL DEFAULT 12,
    foreign_access TEXT NOT NULL, status TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
    in_universe INTEGER NOT NULL DEFAULT 1, loaded_at TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE contract (
    contract_id TEXT PRIMARY KEY, instrument_id TEXT NOT NULL, bbg_ticker TEXT NOT NULL,
    month_code TEXT NOT NULL, year INTEGER NOT NULL, month INTEGER NOT NULL, first_trade_date TEXT,
    last_trade_date TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (instrument_id, year, month)
) WITHOUT ROWID;
CREATE TABLE price_daily (
    contract_id TEXT NOT NULL, date TEXT NOT NULL, settle REAL NOT NULL, open_interest REAL, volume REAL,
    PRIMARY KEY (contract_id, date)
) WITHOUT ROWID;
CREATE TABLE fx_daily (pair TEXT NOT NULL, date TEXT NOT NULL, rate REAL NOT NULL,
                       PRIMARY KEY (pair, date)) WITHOUT ROWID;
"""
# (contract, root, year, month, last trade date = our expiry)
_CM_CONTRACTS = [
    (CLZ6, "NYMEX:CL", 2026, 12, "2026-11-20"),
    (CLF7, "NYMEX:CL", 2027, 1, "2026-12-18"),
    (NGX6, "NYMEX:NG", 2026, 11, "2026-10-28"),
    (CUZ6, "SHFE:CU", 2026, 12, "2026-12-15"),
]
_CM_ROOTS = [("NYMEX:CL", "CL", "USD"), ("NYMEX:NG", "NG", "USD"), ("SHFE:CU", "CU", "CNY")]


def _contract(cid):
    return next(c for c in _CM_CONTRACTS if c[0] == cid)


def _changes(base: float, special: dict) -> pd.Series:
    """Daily raw price changes on C_DATES (the first is the level's start, not a change)."""
    c = pd.Series(base, index=C_DATES)
    for d, v in special.items():
        c.loc[pd.Timestamp(d)] = v
    for k in range(15, 160, 10):                       # small dips inside the VaR window
        c.iloc[len(C_DATES) - k] = -abs(base) * 3
    c.iloc[0] = 0.0
    return c


def _commodity_prices() -> dict:
    z = _changes(0.01, {WTI_NEG: -30.0, WTI_AFTER: -8.0, WORST_CL: -5.0})
    f = z.copy()
    f.loc[pd.Timestamp(SPREAD_DAY)] = 0.0             # z moves +0.01 that day, f does not: the spread moves
    f.loc[pd.Timestamp(WTI_NEG)] = -20.0              # the far month falls less on the negative-WTI day
    ng = _changes(-0.002, {"2022-03-07": 0.9, "2021-02-16": -0.6})
    cu = _changes(5.0, {"2022-03-08": -2000.0, "2020-03-16": -900.0})
    return {CLZ6: 60.0 + z.cumsum(), CLF7: 59.0 + f.cumsum(), NGX6: 3.0 + ng.cumsum(), CUZ6: 60000.0 + cu.cumsum()}


def _usdcnh() -> pd.Series:
    return pd.Series(7.0 + 0.2 * np.sin(np.arange(len(C_DATES)) / 50.0), index=C_DATES)


def write_research_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(_CM_DDL)
    for iid, code, ccy in _CM_ROOTS:
        conn.execute("INSERT INTO instrument (instrument_id, name, sector, subsector, exchange, country, exchange_code, "
                     "bbg_root, bbg_yellow_key, currency, contract_size, size_unit, quote_unit, price_scale, "
                     "foreign_access, status, loaded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (iid, iid, "s", "ss", iid.split(":")[0], "US", code, code, "Comdty", ccy, 1.0, "t", f"{ccy}/t",
                      1.0, "international", "active", "2026-01-01T00:00:00Z"))
    for cid, iid, year, month, ltd in _CM_CONTRACTS:
        conn.execute("INSERT INTO contract VALUES (?,?,?,?,?,?,?,?,?)",
                     (cid, iid, cid, MONTH_CODES[month - 1], year, month, None, ltd, "2026-01-01T00:00:00Z"))
    for cid, px in _commodity_prices().items():
        conn.executemany("INSERT INTO price_daily VALUES (?,?,?,?,?)",
                         [(cid, d.strftime("%Y-%m-%d"), float(v), 1.0, 1.0) for d, v in px.items()])
    conn.executemany("INSERT INTO fx_daily VALUES (?,?,?)",
                     [("USDCNH", d.strftime("%Y-%m-%d"), float(v)) for d, v in _usdcnh().items()])
    conn.commit()
    conn.close()
    return path


@pytest.fixture(autouse=True)
def _no_sibling_research_db(tmp_path, monkeypatch):
    """Every test here reads only what it writes: the research database defaults to a path that
    does not exist unless a test names its own."""
    monkeypatch.setenv(ch_mod.ENV_VAR, str(tmp_path / "no-research-db.sqlite"))
    ch_mod._CACHE.clear()


@pytest.fixture
def research(tmp_path):
    return load_commodity_history(write_research_db(tmp_path / "rv.sqlite"))


def _insert_future(conn, tid, cid, contracts, fill, trade_date, account="A"):
    _, root_id, _, _, expiry = _contract(cid)
    root = get_root(root_id)
    conn.execute("INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, "
                 "is_ndf, bbg_ticker, expiry_date) VALUES (?,'FUTURE',?,?,?,0,?,?)",
                 (cid, root_id, root.currency, root.multiplier, cid, expiry))
    conn.execute("INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, "
                 "price, account, counterparty, strategy, trader, description, theme) "
                 "VALUES (?,'XLSX',?,'FUTURE',?,?,?,?,?,'cp','','t','d','')",
                 (tid, cid, tid, trade_date, contracts, fill, account))
    conn.execute("INSERT INTO trade_legs VALUES (?,1,'NOTIONAL',?,?,?,?,?,0)",
                 (tid, root.currency, contracts * root.multiplier * fill, trade_date, expiry, fill))


def _fx_chf(conn):
    """A USDCHF forward long 0.8m CHF, marked at 0.64: $1.25m of CHF."""
    conn.execute("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                 "bbg_ticker, expiry_date) VALUES ('USDCHF','FX','USD','CHF',1,0,'USDCHF Curncy','9999-12-31')")
    conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("c1", "XLSX", "USDCHF", "FX_FWD", "c1", "2026-09-01", -1_000_000.0, 0.8, "acc", "cp", "", "t", "d", ""))
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("c1", 1, "FX_NEAR", "USD", -1_000_000.0, "2026-09-01", "2026-10-20", 0.8, 1),
        ("c1", 2, "FX_NEAR", "CHF", 800_000.0, "2026-09-01", "2026-10-20", 0.8, 1)])
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
                 (C_AS_OF, "USDCHF", C_AS_OF, "SPOT", 0.64, "BBG_BFXFORWARD", f"{C_AS_OF}T15:00:00-04:00"))


def _fx_only_chf():
    conn = schema.connect()
    _fx_chf(conn)
    conn.commit()
    return conn


def _commodity_book(*, fx: bool = False):
    """+2 CLZ26 / -2 CLF27 on one day and account (a calendar spread), +1 CLZ26 another day
    (an outright), -1 NGX26 (energy), +3 SHFE CUZ26 (metals, CNY); `fx` adds the USDCHF forward."""
    conn = schema.connect()
    _insert_future(conn, "W1", CLZ6, 2, 70.0, "2026-09-01")
    _insert_future(conn, "W2", CLF7, -2, 69.5, "2026-09-01")
    _insert_future(conn, "W3", CLZ6, 1, 71.0, "2026-09-02")
    _insert_future(conn, "N1", NGX6, -1, 3.1, "2026-09-03")
    _insert_future(conn, "C1", CUZ6, 3, 80000.0, "2026-09-04")
    marks = [(CLZ6, "2026-11-20", "FUTURE_PX", 72.0, "BBG_BDH"), (CLF7, "2026-12-18", "FUTURE_PX", 71.0, "BBG_BDH"),
             (NGX6, "2026-10-28", "FUTURE_PX", 3.0, "BBG_BDH"), (CUZ6, "2026-12-15", "FUTURE_PX", 80500.0, "BBG_BDH"),
             ("USDCNY", C_AS_OF, "SPOT", USDCNY_SPOT, "BBG_BFXFORWARD")]
    conn.execute("INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, "
                 "is_ndf, bbg_ticker, expiry_date) VALUES ('USDCNY','FX','USD','CNY',1,0,'USDCNY Curncy','9999-12-31')")
    for inst, settle, mt, value, source in marks:
        conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
                     (C_AS_OF, inst, settle, mt, value, source, f"{C_AS_OF}T15:00:00-04:00"))
    if fx:
        _fx_chf(conn)
    conn.commit()
    return conn


def _hand_pnl(cid: str, lots: float) -> pd.Series:
    """lots x our multiplier x raw settle change x USD per quote unit (USDCNH for CNY), by hand."""
    root = get_root(_contract(cid)[1])
    s = _commodity_prices()[cid].diff().iloc[1:] * root.multiplier * lots
    if root.currency == "CNY":
        s = s / _usdcnh().reindex(s.index)
    return s


def _book_hand() -> pd.Series:
    return _hand_pnl(CLZ6, 3) + _hand_pnl(CLF7, -2) + _hand_pnl(NGX6, -1) + _hand_pnl(CUZ6, 3)


def _no_fx_history(tmp_path):
    return load_history(tmp_path / "no-fx-history")


def _by_key(out):
    return {r["key"]: r for r in out["underlyers"]}


def _run(research, tmp_path, conn=None, **kw):
    return book_risk(conn or _commodity_book(), C_AS_OF, history=kw.pop("history", None) or _no_fx_history(tmp_path),
                     commodity_history=research, **kw)


def test_a_commodity_underlyer_is_its_contracts_at_their_delta_lots(research, tmp_path):
    out = _run(research, tmp_path)
    cl = _by_key(out)["COMMODITY:NYMEX:CL"]
    assert (cl["kind"], cl["role"], cl["underlyer"], cl["sector"]) == ("COMMODITY", "part", "NYMEX:CL", "energy")
    assert [(c["contract_id"], c["delta_lots"], c["in_series"]) for c in cl["contracts"]] == \
        [(CLZ6, 3.0, True), (CLF7, -2.0, True)]
    hand = _hand_pnl(CLZ6, 3) + _hand_pnl(CLF7, -2)
    assert hand.min() == pytest.approx(3 * 1000 * -30.0 - 2 * 1000 * -20.0)
    assert (cl["worst_1d_raw_usd"], cl["worst_1d_raw_date"]) == (pytest.approx(hand.min()), WTI_NEG)
    lag2 = pd.Timestamp(C_AS_OF) - pd.offsets.BusinessDay(2)
    trailing = hand.loc[:lag2].iloc[-500:].std() * math.sqrt(252)
    assert cl["vol_trailing_ann_usd"] == pytest.approx(trailing, rel=1e-9)
    assert cl["var95_1d_usd"] == pytest.approx(-np.percentile(hand.iloc[-252:].to_numpy(), 5))
    assert cl["days"] == len(C_DATES) - 1 and cl["last_date"] == C_AS_OF
    # the USD delta is curve-positions' own, never recomputed: 3 x 1000 x 72 - 2 x 1000 x 71
    assert cl["net_usd"] == pytest.approx(3 * 1000 * 72.0 - 2 * 1000 * 71.0)
    assert cl["gross_usd"] == pytest.approx(3 * 1000 * 72.0 + 2 * 1000 * 71.0)
    assert out["commodity_history"]["available"] and out["commodity_history"]["used_to"] == C_AS_OF
    assert out["commodity_history"]["lag2_date"] == lag2.strftime("%Y-%m-%d")


def test_a_cny_contract_converts_at_the_research_usdcnh(research, tmp_path):
    cu = _by_key(_run(research, tmp_path))["COMMODITY:SHFE:CU"]
    detail = cu["contracts"][0]
    assert (detail["contract_id"], detail["currency"], detail["fx_pair"], detail["fx_missing_days"]) == \
        (CUZ6, "CNY", "USDCNH", 0)
    hand = _hand_pnl(CUZ6, 3)
    assert hand.loc["2022-03-08"] == pytest.approx(3 * 5 * -2000.0 / _usdcnh().loc["2022-03-08"])
    assert (cu["worst_1d_raw_usd"], cu["worst_1d_raw_date"]) == (pytest.approx(hand.min()), "2022-03-08")
    # the delta in USD is at our official USDCNY spot (curve-positions), the history at USDCNH
    assert cu["net_usd"] == pytest.approx(3 * 5 * 80500.0 / USDCNY_SPOT)


def test_a_spread_underlyer_is_its_legs_at_their_open_lots(research, tmp_path):
    out = _run(research, tmp_path)
    spreads = [r for r in out["underlyers"] if r["kind"] == "SPREAD"]
    assert len(spreads) == 1
    sp = spreads[0]
    assert sp["role"] == "view" and sp["spread_kind"] == "calendar" and sp["key"].startswith("SPREAD:")
    assert sorted((leg["contract_id"], leg["open_lots"]) for leg in sp["legs"]) == [(CLF7, -2.0), (CLZ6, 2.0)]
    hand = _hand_pnl(CLZ6, 2) + _hand_pnl(CLF7, -2)
    assert hand.loc[SPREAD_DAY] == pytest.approx(2 * 1000 * 0.01)
    assert (sp["worst_1d_raw_usd"], sp["worst_1d_raw_date"]) == (pytest.approx(hand.min()), WTI_NEG)
    assert sp["var95_1d_usd"] == pytest.approx(-np.percentile(hand.iloc[-252:].to_numpy(), 5))
    # a clean 1:1 calendar leaves no outright: its net / gross USD are the leftover's, zero
    assert sp["net_usd"] == pytest.approx(0.0) and sp["gross_usd"] == pytest.approx(0.0)


def test_a_sector_is_the_sum_of_its_commodities(research, tmp_path):
    out = _run(research, tmp_path)
    rows = _by_key(out)
    energy, metals = rows["SECTOR:energy"], rows["SECTOR:metals"]
    assert (energy["kind"], energy["role"]) == ("SECTOR", "view")
    assert energy["parts"] == ["COMMODITY:NYMEX:CL", "COMMODITY:NYMEX:NG"] and metals["parts"] == ["COMMODITY:SHFE:CU"]
    hand = _hand_pnl(CLZ6, 3) + _hand_pnl(CLF7, -2) + _hand_pnl(NGX6, -1)
    assert (energy["worst_1d_raw_usd"], energy["worst_1d_raw_date"]) == (pytest.approx(hand.min()), WTI_NEG)
    assert energy["var95_1d_usd"] == pytest.approx(-np.percentile(hand.iloc[-252:].to_numpy(), 5))
    assert energy["net_usd"] == pytest.approx(rows["COMMODITY:NYMEX:CL"]["net_usd"] + rows["COMMODITY:NYMEX:NG"]["net_usd"])
    assert metals["worst_1d_raw_usd"] == pytest.approx(rows["COMMODITY:SHFE:CU"]["worst_1d_raw_usd"])
    # the parts first, then the sectors, then the spreads
    kinds = [r["kind"] for r in out["underlyers"]]
    assert kinds == sorted(kinds, key=lambda k: {"SECTOR": 1, "SPREAD": 2}.get(k, 0))


def test_the_book_is_the_parts_alone_with_no_double_count(research, tmp_path):
    out = _run(research, tmp_path)
    book = out["book"]
    assert book["rows_in_series"] == ["NYMEX:CL", "SHFE:CU", "NYMEX:NG"]          # by gross USD
    spread_names = {r["underlyer"] for r in out["underlyers"] if r["kind"] == "SPREAD"}
    assert set(book["views"]) == {"energy", "metals"} | spread_names
    hand = _book_hand()
    assert (book["worst_1d_raw_usd"], book["worst_1d_raw_date"]) == (pytest.approx(hand.min()), WTI_NEG)
    assert book["var_window"]["pnl_usd"] == pytest.approx(list(hand.iloc[-252:]))
    assert book["lag2_date"] == out["commodity_history"]["lag2_date"]
    # the FX figures are the dashboard's FX-legs net (none here); the commodity delta is apart
    assert math.isnan(book["net_usd"]) and math.isnan(book["gross_usd"])
    rows = _by_key(out)
    parts = [rows[k] for k in ("COMMODITY:NYMEX:CL", "COMMODITY:NYMEX:NG", "COMMODITY:SHFE:CU")]
    assert book["commodity_net_usd"] == pytest.approx(sum(r["net_usd"] for r in parts))
    assert book["commodity_gross_usd"] == pytest.approx(sum(r["gross_usd"] for r in parts))
    assert book["commodity_reason"] == ""


def test_shock_days_are_zeroed_in_the_worst_day_ex_shocks(research, tmp_path):
    cfg = load_config()
    names = {d["date"] for d in cfg["shock_dates"]}
    assert {"2015-01-15", "2016-06-24", WTI_NEG, WTI_AFTER, "2022-03-07", "2022-03-08"} <= names
    rows = _by_key(_run(research, tmp_path, config=cfg))
    cl, cu = rows["COMMODITY:NYMEX:CL"], rows["COMMODITY:SHFE:CU"]
    hand = _hand_pnl(CLZ6, 3) + _hand_pnl(CLF7, -2)
    assert (cl["worst_1d_ex_shocks_usd"], cl["worst_1d_ex_shocks_date"]) == (pytest.approx(hand.loc[WORST_CL]), WORST_CL)
    assert cl["worst_1d_raw_date"] == WTI_NEG                                     # the raw figure keeps it
    assert cu["worst_1d_ex_shocks_date"] == "2020-03-16" and cu["worst_1d_raw_date"] == "2022-03-08"
    cfg["shock_dates"] = [d for d in cfg["shock_dates"] if d["date"] < "2020-01-01"]
    cl = _by_key(_run(research, tmp_path, config=cfg))["COMMODITY:NYMEX:CL"]
    assert cl["worst_1d_ex_shocks_date"] == WTI_NEG


def test_no_crisis_window_in_the_research_history_is_trailing_vol_with_its_reason(research, tmp_path):
    out = _run(research, tmp_path)
    for r in [*out["underlyers"], out["book"]]:
        assert r["vol_note"] == CRISIS_FALLBACK == "crisis window not in history: trailing vol only"
        assert CRISIS_FALLBACK in r["reason"]
        assert r["vol_crisis_ann_usd"] == r["vol_trailing_ann_usd"]
        assert r["vol_blended_ann_usd"] == pytest.approx(r["vol_trailing_ann_usd"])


def test_no_research_database_is_na_with_the_reason_and_keeps_the_positions(tmp_path):
    out = _run(load_commodity_history(tmp_path / "absent.sqlite"), tmp_path)
    ch = out["commodity_history"]
    assert not ch["available"] and "no commodity history database" in ch["reason"]
    rows = [r for r in out["underlyers"] if r["kind"] in ("COMMODITY", "SECTOR", "SPREAD")]
    assert {r["kind"] for r in rows} == {"COMMODITY", "SECTOR", "SPREAD"}
    for r in rows:
        assert math.isnan(r["vol_blended_ann_usd"]) and math.isnan(r["worst_1d_raw_usd"]) and r["days"] == 0
        assert "no commodity history database" in r["reason"] and r["reasons"]["all"]
    cl = _by_key(out)["COMMODITY:NYMEX:CL"]
    assert cl["net_usd"] == pytest.approx(3 * 1000 * 72.0 - 2 * 1000 * 71.0)       # the delta stays
    assert math.isnan(out["book"]["vol_blended_ann_usd"]) and "commodities: " in out["book"]["reason"]
    assert any(m.startswith("NYMEX:CL: not in the book series (") for m in out["missing"])


def _memory_fx_history() -> History:
    """An in-memory nm-dashboard history (no parquet): CHF, spot alone, 2007 to C_AS_OF."""
    dates = pd.bdate_range("2007-01-01", C_AS_OF)
    r = pd.Series(BASE, index=dates)
    r.loc[pd.Timestamp(SNB)] = -0.15
    r.loc[pd.Timestamp("2020-03-16")] = -0.03
    spot = pd.DataFrame({"CHF": 1.1 * np.exp(r.cumsum())}, index=dates)
    spot.index.name = "date"
    files = {"spot": {"file": history_mod.SPOT_FILE, "path": "memory", "loaded": True, "rows": len(spot),
                      "columns": ["CHF"], "first_date": "2007-01-01", "last_date": C_AS_OF, "reason": ""},
             "yields": {"file": history_mod.YIELDS_FILE, "path": "memory", "loaded": False, "rows": 0, "columns": [],
                        "first_date": None, "last_date": None, "reason": "not in this test"}}
    return History(True, "memory", spot=spot, yields=pd.DataFrame(), last_date=C_AS_OF, files=files)


def test_the_fx_part_is_unchanged_by_the_commodity_rows(research, tmp_path):
    fx_hist = _memory_fx_history()
    fx_only = _run(research, tmp_path, conn=_fx_only_chf(), history=fx_hist)
    both = _run(research, tmp_path, conn=_commodity_book(fx=True), history=fx_hist)
    a, b = _by_key(fx_only)["CHF"], _by_key(both)["CHF"]
    for k in ("net_usd", "gross_usd", "vol_blended_ann_usd", "vol_trailing_ann_usd", "vol_crisis_ann_usd",
              "var95_1d_usd", "worst_1d_raw_usd", "worst_1d_raw_date", "worst_1d_ex_shocks_usd",
              "worst_1d_ex_shocks_date", "days", "reason", "kind", "role"):
        assert a[k] == b[k], k
    assert a["net_usd"] == pytest.approx(1_250_000.0) and a["kind"] == KIND_FX and a["vol_note"] == ""
    assert (a["worst_1d_raw_date"], a["worst_1d_ex_shocks_date"]) == (SNB, "2020-03-16")
    for k in ("net_usd", "gross_usd", "fx_net_usd", "fx_gross_usd"):
        assert fx_only["book"][k] == pytest.approx(both["book"][k]), k
    assert fx_only["scenarios"] == both["scenarios"]
    assert fx_only["history"] == both["history"]
    assert both["book"]["rows_in_series"] == ["CHF", "NYMEX:CL", "SHFE:CU", "NYMEX:NG"]   # the parts by gross
    # the book is FX + commodities on the union of their dates, summed where both have a day
    fx_pnl = pd.Series(1_250_000.0 * np.log(fx_hist.spot["CHF"]).diff().iloc[1:].to_numpy(),
                       index=fx_hist.spot.index[1:])
    total = pd.concat([fx_pnl, _book_hand()], axis=1).sum(axis=1, min_count=1)
    assert both["book"]["worst_1d_raw_usd"] == pytest.approx(total.min())
    assert both["book"]["worst_1d_raw_date"] == SNB


def test_commodity_scenarios_are_engine_stress_passed_through(research, tmp_path, monkeypatch):
    import engine.stress as stress_pkg
    conn = _commodity_book()
    cs = _run(research, tmp_path, conn=conn)["commodity_scenarios"]
    assert {"as_of", "available", "config", "scenarios", "reasons"} <= set(cs)
    direct = stress_pkg.commodity_stress(conn, C_AS_OF, history=research.window_move)
    assert [s["name"] for s in cs["scenarios"]] == [s["name"] for s in direct["scenarios"]]
    for got, exp in zip(cs["scenarios"], direct["scenarios"]):
        assert (got["total_usd"] is None) == (exp["total_usd"] is None)
        if exp["total_usd"] is not None:
            assert got["total_usd"] == pytest.approx(exp["total_usd"])

    def boom(*a, **k):
        raise RuntimeError("scenario file broken")
    monkeypatch.setattr(stress_pkg, "commodity_stress", boom)     # an error there is a reason, never a crash
    cs = _run(research, tmp_path, conn=conn)["commodity_scenarios"]
    assert cs["available"] is False and cs["scenarios"] == [] and "scenario file broken" in cs["reasons"][0]


def test_the_vol_target_is_the_placeholder_and_says_so():
    cfg = load_config()
    assert cfg["vol_target_usd"] == 4.5e6 and cfg["vol_target_placeholder"] is True
    assert "docs/open-questions.md C5" in cfg["vol_target_note"]


def test_book_risk_with_commodities_writes_nothing_and_is_json_friendly(research, tmp_path):
    conn = _commodity_book()
    changes = conn.total_changes
    out = _run(research, tmp_path, conn=conn)
    assert conn.total_changes == changes
    json.dumps(out)


def test_an_option_reads_its_underlying_and_an_lme_forward_its_prompt_month(research):
    from engine.risk.commodity import _PerLot, contract_series, lme_history_contract
    per_lot = _PerLot(research, C_AS_OF)
    option = {"product": "CMDTY_OPTION", "root_id": "NYMEX:CL", "contract_id": "CLZ6C 75 Comdty",
              "underlying_id": CLZ6, "delta_lots": 0.5, "multiplier": 1000.0, "currency": "USD", "reason": ""}
    detail, s = contract_series(option, per_lot, C_AS_OF)
    assert detail["history_contract"] == CLZ6 and "underlying" in detail["note"]
    assert s.to_numpy() == pytest.approx(_hand_pnl(CLZ6, 0.5).to_numpy())
    # an option with no delta mark is left out with curve-positions' reason
    detail, s = contract_series({**option, "delta_lots": None, "reason": "no DELTA mark"}, per_lot, C_AS_OF)
    assert s is None and detail["reason"] == "no DELTA mark" and not detail["in_series"]
    # an LME forward: its prompt month's contract when the research app has it, else the nearest listed one
    cid, note, why = lme_history_contract(research, "NYMEX:CL", 2026, 12, "2026-12-16", C_AS_OF)
    assert (cid, why) == (CLZ6, "") and "prompt month" in note
    cid, note, why = lme_history_contract(research, "NYMEX:CL", 2027, 3, "2027-03-17", C_AS_OF)
    assert (cid, why) == (CLF7, "") and "nearest" in note
    cid, note, why = lme_history_contract(research, "LME:CA", 2026, 12, "2026-12-16", C_AS_OF)
    assert cid is None and "LME:CA is not in the research database" in why
