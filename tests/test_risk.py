"""engine/risk: the Risk tab's metrics (the nm-dashboard's definitions on the book's own
positions), on synthetic parquet history written to tmp_path; never on the sibling repo.
The macro trader's rates (DV01) and equity-index (ES + SPX) underlyers left in Phase 2
(user approval 2026-09-24): `book_positions`' `rates` and `equity_index` blocks are not read."""
import math
import os

import numpy as np
import pandas as pd
import pytest

from data.ingest import schema
from engine.pnl import stress
from engine.risk import book_risk, load_config, load_history
from engine.risk import history as history_mod
from engine.risk.config import DEFAULTS
from engine.risk.metrics import DELTA_KINDS, KIND_FX, KIND_METAL, rows_from_positions, unit_moves

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
    assert [d["date"] for d in cfg["shock_dates"]] == ["2015-01-15", "2016-06-24"]
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
    for key in ("vol_target_usd", "stress_pct", "var_window_bd", "var_confidence", "worst_day_start", "shock_dates"):
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
