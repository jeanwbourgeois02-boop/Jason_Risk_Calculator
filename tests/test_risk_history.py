"""engine/risk/history.py without a parquet engine: the file list, the status shape, the
folder search and the reasons given when there is nothing to read. The parquet reads
themselves are exercised by tests/test_risk.py (risk-metrics'), which needs pyarrow.

engine/risk/commodity_history.py on a small synthetic copy of the research app's
database (the four tables it reads, their columns as in `rvapp/db/schema.sql`), built in
WAL mode like the real one."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3

import pandas as pd
import pytest

from engine.risk import commodity_history as ch_mod
from engine.risk import history as history_mod
from engine.risk.commodity_history import load_commodity_history
from engine.risk.history import FILES, SPOT_FILE, YIELDS_FILE, History, load_history

STATUS_KEYS = {"available", "path", "last_date", "reason", "note", "candidates", "files"}
FILE_STATUS_KEYS = {"file", "path", "loaded", "rows", "columns", "first_date", "last_date", "reason"}


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv(history_mod.ENV_VAR, raising=False)
    monkeypatch.delenv(ch_mod.ENV_VAR, raising=False)
    history_mod._CACHE.clear()
    history_mod._LAST_DATE_MEMO.clear()
    ch_mod._CACHE.clear()


def test_the_files_read_are_spot_and_yields_only():
    assert FILES == {"spot": "bbg_raw_fx_marks.parquet", "yields": "bbg_raw_fx_yields.parquet"}
    assert not hasattr(history_mod, "RATES_FILE")                 # the par swap rate file is retired (Phase 2)
    assert "swap_rates" not in History.__dataclass_fields__


def test_a_missing_folder_is_not_available_and_names_the_path(tmp_path):
    absent = tmp_path / "nowhere"
    h = load_history(absent)
    assert not h.available and h.reason == f"no market history folder: tried {absent}"
    status = h.status()
    assert set(status) == STATUS_KEYS and status["files"] == {} and status["last_date"] is None
    assert status["candidates"] == [{"path": str(absent), "exists": False, "last_date": None}]
    json.dumps(status)                                             # JSON-friendly


def test_no_default_folder_names_every_path_tried(tmp_path, monkeypatch):
    dirs = (tmp_path / "a", tmp_path / "b", tmp_path / "c")
    monkeypatch.setattr(history_mod, "DEFAULT_DIRS", dirs)
    h = load_history()
    assert not h.available
    assert h.reason == "no market history folder: tried " + ", ".join(str(d) for d in dirs)
    assert [c["path"] for c in h.status()["candidates"]] == [str(d) for d in dirs]


def test_the_environment_variable_is_the_only_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(history_mod, "DEFAULT_DIRS", (tmp_path / "default",))
    (tmp_path / "default").mkdir()
    monkeypatch.setenv(history_mod.ENV_VAR, str(tmp_path / "env"))
    h = load_history()
    assert not h.available and h.reason == f"no market history folder: tried {tmp_path / 'env'}"


def test_a_folder_without_files_names_each_missing_file(tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()
    h = load_history(folder)
    assert not h.available and h.reason == f"no spot history: {folder / SPOT_FILE} not found"
    status = h.status()
    assert set(status) == STATUS_KEYS and set(status["files"]) == {"spot", "yields"}
    for name, filename in FILES.items():
        f = status["files"][name]
        assert set(f) == FILE_STATUS_KEYS
        assert (f["file"], f["loaded"], f["rows"], f["columns"]) == (filename, False, 0, [])
        assert f["reason"] == f"{folder / filename} not found"
    assert status["files"]["yields"]["path"] == str(folder / YIELDS_FILE)
    json.dumps(status)


def test_an_unreadable_spot_file_is_a_reason_never_an_exception(tmp_path):
    folder = tmp_path / "bad"
    folder.mkdir()
    (folder / SPOT_FILE).write_bytes(b"not a parquet file")
    h = load_history(folder)
    assert not h.available and h.reason.startswith(f"no spot history: {folder / SPOT_FILE} could not be read (")
    assert history_mod.spot_last_date(folder) is None
    assert history_mod.spot_last_date(tmp_path / "absent") is None


def test_the_cache_key_covers_the_read_files_alone(tmp_path):
    folder = tmp_path / "k"
    folder.mkdir()
    (folder / "bbg_raw_rates.parquet").write_bytes(b"x")          # a retired file on disk is ignored
    key = history_mod._cache_key(folder)
    assert key == (str(folder), ((SPOT_FILE, None), (YIELDS_FILE, None)))
    assert load_history(folder) is load_history(folder)           # cached while nothing changes


def test_default_folders_with_no_spot_file_keep_their_order(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setattr(history_mod, "DEFAULT_DIRS", (a, b))
    folder, seen, note = history_mod.history_dir()
    assert folder == a and [c["exists"] for c in seen] == [True, True]
    assert note == f"2 copies found, using {a} (no spot file); {b} has no spot file"


# --------------------------------------------------------------------------------------
# engine/risk/commodity_history.py
# --------------------------------------------------------------------------------------

_DDL = """
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
    contract_id TEXT PRIMARY KEY, instrument_id TEXT NOT NULL REFERENCES instrument (instrument_id),
    bbg_ticker TEXT NOT NULL, month_code TEXT NOT NULL, year INTEGER NOT NULL, month INTEGER NOT NULL,
    first_trade_date TEXT, last_trade_date TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE (instrument_id, year, month)
) WITHOUT ROWID;
CREATE TABLE price_daily (
    contract_id TEXT NOT NULL REFERENCES contract (contract_id), date TEXT NOT NULL,
    settle REAL NOT NULL, open_interest REAL, volume REAL, PRIMARY KEY (contract_id, date)
) WITHOUT ROWID;
CREATE TABLE fx_daily (
    pair TEXT NOT NULL, date TEXT NOT NULL, rate REAL NOT NULL, PRIMARY KEY (pair, date)
) WITHOUT ROWID;
"""

D5, D6, D7, D8, D9 = "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"
MONTHS = "FGHJKMNQUVXZ"

# (instrument_id, research bbg_root, currency, price_scale)
_INSTRUMENTS = [
    ("CBOT:ZC", "C", "USD", 0.01),        # quoted in cents: raw x 0.01 = USD/bu
    ("SHFE:RB", "RBT", "CNY", 1.0),
    ("SHFE:WR", "ZZWR", "CNY", 1.0),      # the research app's placeholder root; ours is 'WR'
    ("OSE:JRU", "JRU", "JPY", 1.0),       # no USDJPY on file
]
# (contract_id, instrument_id, year, month, last_trade_date)
_CONTRACTS = [
    ("C H26 Comdty", "CBOT:ZC", 2026, 3, D7),          # expires on the 7th: the roll day
    ("C K26 Comdty", "CBOT:ZC", 2026, 5, "2026-02-06"),
    ("C N26 Comdty", "CBOT:ZC", 2026, 7, "2026-03-06"),  # first settles on the 7th
    ("C U26 Comdty", "CBOT:ZC", 2026, 9, "2026-04-06"),  # listed, never settled (beyond the kept depth)
    ("RBTF27 Comdty", "SHFE:RB", 2027, 1, "2027-01-15"),
    ("ZZWRF27 Comdty", "SHFE:WR", 2027, 1, "2027-01-15"),
    ("JRUF27 Comdty", "OSE:JRU", 2027, 1, "2027-01-15"),
]
_PRICES = {
    "C H26 Comdty": {D5: 400, D6: 402, D7: 405},
    "C K26 Comdty": {D5: 410, D6: 411, D7: 415, D8: 420, D9: 418},
    "C N26 Comdty": {D7: 430, D8: 433, D9: 440},
    "RBTF27 Comdty": {D5: 3000, D6: 3010, D7: 3005, D8: 3020},
    "ZZWRF27 Comdty": {D5: 100, D6: 101},
    "JRUF27 Comdty": {D5: 300, D6: 301},
}
_FX = {
    "USDCNH": {D5: 7.0, D6: 7.2, D7: 7.1, D9: 7.3},      # none on the 8th: the 7th's rate carries
    "USDCNY": {D5: 7.5, D6: 7.5, D7: 7.5, D8: 7.5, D9: 7.5},
}


def _build(path, wal=True):
    conn = sqlite3.connect(path)
    if wal:
        conn.execute("PRAGMA journal_mode=wal")
    conn.executescript(_DDL)
    for iid, root, ccy, scale in _INSTRUMENTS:
        exch, code = iid.split(":")
        conn.execute("INSERT INTO instrument (instrument_id, name, sector, subsector, exchange, country, "
                     "exchange_code, bbg_root, bbg_yellow_key, currency, contract_size, size_unit, quote_unit, "
                     "price_scale, foreign_access, status, loaded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (iid, iid, "s", "ss", exch, "US", code, root, "Comdty", ccy, 1.0, "t", f"{ccy}/t", scale,
                      "international", "active", "2026-01-01T00:00:00Z"))
    for cid, iid, year, month, ltd in _CONTRACTS:
        conn.execute("INSERT INTO contract VALUES (?,?,?,?,?,?,?,?,?)",
                     (cid, iid, cid, MONTHS[month - 1], year, month, None, ltd, "2026-01-01T00:00:00Z"))
    for cid, rows in _PRICES.items():
        conn.executemany("INSERT INTO price_daily VALUES (?,?,?,?,?)",
                         [(cid, d, s, 10.0, 1.0) for d, s in rows.items()])
    for pair, rows in _FX.items():
        conn.executemany("INSERT INTO fx_daily VALUES (?,?,?)", [(pair, d, r) for d, r in rows.items()])
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def rv(tmp_path):
    return _build(tmp_path / "rv.sqlite")


def _ts(*days):
    return [pd.Timestamp(d) for d in days]


def test_commodity_status_of_a_loaded_database(rv):
    h = load_commodity_history(rv)
    assert h.available and h.reason == ""
    status = h.status()
    assert set(status) == {"available", "path", "reason", "first_date", "last_date", "note", "candidates",
                           "roots", "contracts", "fx_pairs"}
    assert (status["first_date"], status["last_date"], status["roots"], status["contracts"]) == (D5, D9, 4, 7)
    assert status["fx_pairs"] == ["USDCNH", "USDCNY"]
    assert status["note"] == f"using {rv} (settlements {D5} to {D9})"
    json.dumps(status)


def test_the_price_scale_is_applied(rv):
    s = load_commodity_history(rv).settle_series("C K26 Comdty")
    assert list(s.index) == _ts(D5, D6, D7, D8, D9)
    assert s.tolist() == pytest.approx([4.10, 4.11, 4.15, 4.20, 4.18])      # cents x 0.01 = USD/bu
    assert s.attrs["reason"] == "" and s.attrs["price_scale"] == 0.01


def test_a_contract_id_is_matched_on_root_year_and_month_when_the_roots_differ(rv):
    h = load_commodity_history(rv)
    s = h.settle_series("WRF27 Comdty", root_id="SHFE:WR")        # ours 'WR', the research app's 'ZZWR'
    assert s.tolist() == [100.0, 101.0] and s.attrs["research_contract_id"] == "ZZWRF27 Comdty"
    assert h.settle_series("WRF27 Comdty").attrs["reason"] == f"contract WRF27 Comdty is not in the research database ({rv})"


def test_constant_maturity_rolls_on_the_last_trade_date(rv):
    h = load_commodity_history(rv)
    front = h.constant_maturity_series("CBOT:ZC", 1)
    assert front.tolist() == pytest.approx([4.00, 4.02, 4.15, 4.20, 4.18])
    assert [front.attrs["contracts"][d] for d in _ts(D5, D6, D7, D8, D9)] == \
        ["C H26 Comdty", "C H26 Comdty", "C K26 Comdty", "C K26 Comdty", "C K26 Comdty"]
    change = h.constant_maturity_changes("CBOT:ZC", 1)
    # the roll day's change is K26's own move (415 - 411), never K26 - H26 (415 - 402)
    assert dict(zip(change.index, change.tolist())) == pytest.approx(
        {pd.Timestamp(D6): 0.02, pd.Timestamp(D7): 0.04, pd.Timestamp(D8): 0.05, pd.Timestamp(D9): -0.02})
    second = h.constant_maturity_changes("CBOT:ZC", 2, raw=True)
    # N26 did not trade on the 6th, so the 7th (its first day as #2) has no change
    assert dict(zip(second.index, second.tolist())) == {pd.Timestamp(D6): 1.0, pd.Timestamp(D8): 3.0,
                                                        pd.Timestamp(D9): 7.0}
    assert h.constant_maturity_series("CBOT:ZC", 1, start=D8).index.tolist() == _ts(D8, D9)


def test_position_pnl_for_a_cny_contract_is_converted_through_usdcnh(rv):
    h = load_commodity_history(rv)
    s = h.daily_pnl_series_for_position("SHFE:RB", "RBTF27 Comdty", lots=-3, multiplier=10, currency="CNY")
    expected = {pd.Timestamp(D6): 10 * 10 * -3 / 7.2,
                pd.Timestamp(D7): -5 * 10 * -3 / 7.1,
                pd.Timestamp(D8): 15 * 10 * -3 / 7.1}              # no USDCNH on the 8th: the 7th's rate
    assert dict(zip(s.index, s.tolist())) == pytest.approx(expected)
    assert (s.attrs["reason"], s.attrs["fx_pair"], s.attrs["fx_missing_days"]) == ("", "USDCNH", 0)
    assert s.attrs["months_ahead"] == 1 and s.attrs["own_from"] == D5 and s.attrs["fallback_days"] == 0
    cny = h.daily_pnl_series_for_position("SHFE:RB", "RBTF27 Comdty", -3, 10, "CNY", fx="USDCNY")
    assert cny.loc[pd.Timestamp(D6)] == pytest.approx(-300 / 7.5) and cny.attrs["fx_pair"] == "USDCNY"
    given = h.daily_pnl_series_for_position("SHFE:RB", "RBTF27 Comdty", -3, 10, "CNY",
                                            fx=pd.Series([0.1], index=pd.to_datetime([D5])))
    assert given.loc[pd.Timestamp(D6)] == pytest.approx(-30.0) and given.attrs["fx_pair"] == "given"


def test_position_pnl_falls_back_to_constant_maturity_before_listing(rv):
    h = load_commodity_history(rv)
    # N26 is #2 on the strip on the last date (H26 has expired), and first settles on the 7th
    s = h.daily_pnl_series_for_position("CBOT:ZC", "C N26 Comdty", lots=2, multiplier=50, currency="USD")
    # the 6th is #2's (K26's) move of 1 cent; the 8th and 9th are N26's own 3 and 7 cents.
    # Raw cents x our multiplier (50 USD per cent), never quote units (0.01 USD) x 50.
    assert dict(zip(s.index, s.tolist())) == {pd.Timestamp(D6): 100.0, pd.Timestamp(D8): 300.0,
                                              pd.Timestamp(D9): 700.0}
    assert (s.attrs["months_ahead"], s.attrs["own_from"], s.attrs["fallback_days"]) == (2, D7, 1)
    assert s.attrs["fx_pair"] == ""


def test_missing_database_contract_root_and_fx_give_reasons_never_exceptions(rv, tmp_path, monkeypatch):
    absent = tmp_path / "none.sqlite"
    h = load_commodity_history(absent)
    assert not h.available and h.reason == f"no commodity history database: tried {absent}"
    for s in (h.settle_series("C K26 Comdty"), h.constant_maturity_series("CBOT:ZC", 1),
              h.fx_series("USDCNH"), h.daily_pnl_series_for_position("CBOT:ZC", "C K26 Comdty", 1, 50, "USD")):
        assert s.empty and s.attrs["reason"] == h.reason
    json.dumps(h.status())

    monkeypatch.setattr(ch_mod, "DEFAULT_PATH", tmp_path / "default.sqlite")
    assert load_commodity_history().reason == f"no commodity history database: tried {tmp_path / 'default.sqlite'}"
    monkeypatch.setenv(ch_mod.ENV_VAR, str(rv))
    assert load_commodity_history().available                          # the variable wins

    plain = tmp_path / "other.sqlite"
    sqlite3.connect(plain).execute("CREATE TABLE t (x)").connection.close()
    other = load_commodity_history(plain)
    assert not other.available
    assert other.reason == f"{plain} is not the research app's database (no instrument, contract, price_daily, fx_daily table)"
    garbage = tmp_path / "garbage.sqlite"
    garbage.write_bytes(b"not a database at all, just text" * 10)
    assert load_commodity_history(garbage).reason.startswith(f"{garbage} could not be read (")

    h = load_commodity_history(rv)
    assert h.settle_series("CLZ26 Comdty").attrs["reason"] == f"contract CLZ26 Comdty is not in the research database ({rv})"
    assert h.constant_maturity_series("NYMEX:CL", 1).attrs["reason"] == f"root NYMEX:CL is not in the research database ({rv})"
    assert h.constant_maturity_series("CBOT:ZC", 9).attrs["reason"] == \
        "root CBOT:ZC has no settlement for contract #9 in the research database"
    assert h.daily_pnl_series_for_position("NYMEX:CL", "CLZ26 Comdty", 1, 1000, "USD").attrs["reason"] == \
        f"root NYMEX:CL is not in the research database ({rv})"
    assert h.daily_pnl_series_for_position("CBOT:ZC", "C Z26 Comdty", 1, 50, "USD").attrs["reason"] == \
        f"contract C Z26 Comdty (CBOT:ZC Z2026) is not in the research database ({rv})"
    assert h.daily_pnl_series_for_position("CBOT:ZC", "C K26 Comdty", 1, 50, "EUR").attrs["reason"] == \
        "CBOT:ZC is quoted in USD in the research database, not EUR"
    jpy = h.daily_pnl_series_for_position("OSE:JRU", "JRUF27 Comdty", 1, 5000, "JPY")
    assert jpy.empty and jpy.attrs["reason"].startswith("no USD conversion for JPY: FX pair USDJPY is not in the research database")
    assert h.daily_pnl_series_for_position("SHFE:RB", "C K26 Comdty", 1, 10, "CNY").attrs["reason"] == \
        "contract C K26 Comdty belongs to CBOT:ZC in the research database, not SHFE:RB"
    assert h.daily_pnl_series_for_position("CBOT:ZC", "C U26 Comdty", 1, 50, "USD").attrs["reason"] == (
        "contract C U26 Comdty has no day-on-day settlement change in the research database (0 settlement(s) "
        "of its own, none for contract #3 of CBOT:ZC; the research app keeps the nearest 12 contract(s) of CBOT:ZC)")


def test_window_move_holds_one_contract_through_its_expiry(rv):
    h = load_commodity_history(rv)
    # 0 months out on the 5th: H26 (last trade the 7th), held without a roll: its own last settle
    d = h.window_move_detail("CBOT:ZC", 0, D5, D9)
    assert d == {"move": pytest.approx(405 / 400 - 1), "reason": "", "contract_id": "C H26 Comdty",
                 "start_date": D5, "end_date": D7, "start_settle": 400.0, "end_settle": 405.0}
    assert h.window_move("CBOT:ZC", 1, D5, D9) == (pytest.approx(418 / 410 - 1), "")     # K26, 32 days out
    assert ch_mod.window_move("CBOT:ZC", 1, D6, D8, path=rv) == (pytest.approx(420 / 411 - 1), "")
    # a start on a weekend takes the last close before it; the end, the contract's last on or before it
    assert h.window_move("CBOT:ZC", 1, "2026-01-04", D9)[1] == \
        f"root CBOT:ZC has no settlement on or before 2026-01-04 in the research database (it starts {D5})"
    assert h.window_move("CBOT:ZC", 1, D6, "2026-01-10") == (pytest.approx(418 / 411 - 1), "")


def test_window_move_reasons(rv, tmp_path):
    h = load_commodity_history(rv)
    assert h.window_move("CBOT:ZC", 2, D5, D9) == \
        (None, f"contract C N26 Comdty (2 months out on {D5}) has no settlement on that day in the research database")
    assert h.window_move("CBOT:ZC", 3, D9, "2026-01-12") == \
        (None, f"contract C U26 Comdty (3 months out on {D9}) has no settlement on that day in the research database")
    assert h.window_move("CBOT:ZC", 1, D9, "2026-01-12") == \
        (None, f"contract C K26 Comdty has no settlement after {D9} up to 2026-01-12 in the research database")
    assert h.window_move("CBOT:ZC", 1, D9, D5) == (None, f"window {D9} to {D5}: the end is not after the start")
    assert h.window_move("NYMEX:CL", 0, D5, D9) == (None, f"root NYMEX:CL is not in the research database ({rv})")
    absent = tmp_path / "none.sqlite"
    assert ch_mod.window_move("CBOT:ZC", 0, D5, D9, path=absent) == \
        (None, f"no commodity history database: tried {absent}")
    writer = sqlite3.connect(rv)
    try:
        writer.execute("UPDATE price_daily SET settle = 0 WHERE contract_id = 'C H26 Comdty' AND date = ?", (D5,))
        writer.commit()
        assert load_commodity_history(rv).window_move("CBOT:ZC", 0, D5, D9) == \
            (None, f"contract C H26 Comdty settled at 0 on {D5}: no fractional move from a price not above zero")
    finally:
        writer.close()


def _digest(path):
    return hashlib.md5(path.read_bytes()).hexdigest()


def test_the_database_is_read_only_and_no_lock_is_left(rv):
    before = (_digest(rv), rv.stat().st_mtime_ns)
    h = load_commodity_history(rv)
    h.settle_series("C K26 Comdty")
    h.constant_maturity_changes("CBOT:ZC", 1)
    h.daily_pnl_series_for_position("SHFE:RB", "RBTF27 Comdty", 1, 10, "CNY")
    assert (_digest(rv), rv.stat().st_mtime_ns) == before              # nothing written
    conn = ch_mod._connect(rv)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("INSERT INTO fx_daily VALUES ('USDCNH', '2026-01-12', 7.0)")
    finally:
        conn.close()
    writer = sqlite3.connect(rv, timeout=0)                          # no wait: a lock left would fail here
    try:
        writer.execute("BEGIN EXCLUSIVE")
        writer.rollback()
    finally:
        writer.close()


def test_the_cache_is_kept_until_the_database_or_its_wal_changes(rv):
    first = load_commodity_history(rv)
    first.settle_series("C K26 Comdty")
    assert load_commodity_history(rv) is first                         # the read-only open's side files do not count
    writer = sqlite3.connect(rv)
    try:
        writer.execute("INSERT INTO price_daily VALUES ('C K26 Comdty', '2026-01-12', 421, 1, 1)")
        writer.commit()                                                # in WAL: the main file may not move
        second = load_commodity_history(rv)
        assert second is not first and second.last_date == "2026-01-12"
        assert second.settle_series("C K26 Comdty").iloc[-1] == pytest.approx(4.21)
    finally:
        writer.close()
    assert not math.isnan(first.settle_series("C K26 Comdty").iloc[-1])


# --------------------------------------------------------------------------------------
# the kept constant-maturity frames and position P&L (2026-09-25: book_risk rebuilt them per call)
# --------------------------------------------------------------------------------------

def _cm_frame_per_date(h, root, months_ahead):
    """The per-date build the kept frame replaced (the code before 2026-09-25), as the reference."""
    row = h.instruments.loc[root]
    wide = h._root_prices(root)
    diffs = wide.diff()
    ranked = h._ranked(root, months_ahead, wide.index)
    level, change = [], []
    for d, cid in zip(wide.index, ranked):
        if cid is None or cid not in wide.columns:
            level.append(float("nan"))
            change.append(float("nan"))
        else:
            level.append(wide.at[d, cid])
            change.append(diffs.at[d, cid])
    frame = pd.DataFrame({"contract_id": ranked, "raw": level, "raw_change": change}, index=wide.index)
    frame["settle"] = frame["raw"] * float(row["price_scale"])
    frame["change"] = frame["raw_change"] * float(row["price_scale"])
    return frame


def _build_long(path):
    """The fixture plus one root of ten contracts over a year of weekdays with gaps (seeded):
    rolls, holes, a contract with no settlement at all, and ranks beyond the strip."""
    import random
    rnd = random.Random(7)
    _build(path, wal=True)
    conn = sqlite3.connect(path)
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-01-01", "2025-12-31")]
    conn.execute("INSERT INTO instrument (instrument_id, name, sector, subsector, exchange, country, exchange_code, "
                 "bbg_root, bbg_yellow_key, currency, contract_size, size_unit, quote_unit, price_scale, "
                 "calendar_depth, foreign_access, status, loaded_at) VALUES "
                 "('NYMEX:CL', 'CL', 's', 'ss', 'NYMEX', 'US', 'CL', 'CL', 'Comdty', 'USD', 1000, 'bbl', 'USD/bbl', "
                 "1.0, 4, 'international', 'active', 'x')")
    for k in range(10):
        year, month = 2025 + (k + 1) // 12, (k + 1) % 12 + 1
        ltd = (pd.Timestamp(year, month, 1) - pd.offsets.BDay(3)).strftime("%Y-%m-%d")
        cid = f"CL{MONTHS[month - 1]}{year % 100:02d} Comdty"
        conn.execute("INSERT INTO contract VALUES (?,?,?,?,?,?,?,?,?)",
                     (cid, "NYMEX:CL", cid, MONTHS[month - 1], year, month, None, ltd, "x"))
        if k == 6:
            continue                                        # listed, never settled
        rows = [(cid, d, 70 + k + rnd.uniform(-3, 3), 1.0, 1.0) for d in days
                if d <= ltd and rnd.random() > 0.15]
        conn.executemany("INSERT INTO price_daily VALUES (?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return path


def test_the_kept_constant_maturity_frame_is_the_per_date_build(tmp_path):
    h = load_commodity_history(_build_long(tmp_path / "long.sqlite"))
    for root, depth in (("NYMEX:CL", 12), ("CBOT:ZC", 5), ("SHFE:RB", 2)):
        for n in range(1, depth + 1):
            frame, why = h._cm_frame(root, n)
            assert why == ""
            pd.testing.assert_frame_equal(frame, _cm_frame_per_date(h, root, n), check_exact=True)


def test_a_second_call_is_served_from_memory_and_is_identical(rv, monkeypatch):
    h = load_commodity_history(rv)
    builds, reads, windows = [], [], []
    real_build, real_query, real_window = h._build_cm_frame, ch_mod._query, h._window_move_detail
    monkeypatch.setattr(h, "_build_cm_frame", lambda *a: builds.append(a[:2]) or real_build(*a))
    monkeypatch.setattr(ch_mod, "_query", lambda *a: reads.append(a) or real_query(*a))
    monkeypatch.setattr(h, "_window_move_detail", lambda *a: windows.append(a) or real_window(*a))
    calls = [("CBOT:ZC", "C N26 Comdty", 2, 50, "USD"), ("SHFE:RB", "RBTF27 Comdty", -3, 10, "CNY"),
             ("CBOT:ZC", "C U26 Comdty", 1, 50, "USD"),          # an empty series with its reason
             ("NYMEX:CL", "CLZ26 Comdty", 1, 1000, "USD")]       # a root not on file
    first = [h.daily_pnl_series_for_position(*c, as_of=D9) for c in calls]
    n_builds, n_reads = len(builds), len(reads)
    assert n_builds and n_reads
    second = [h.daily_pnl_series_for_position(*c, as_of=D9) for c in calls]
    assert (len(builds), len(reads)) == (n_builds, n_reads)             # nothing rebuilt, nothing read
    for a, b in zip(first, second):
        pd.testing.assert_series_equal(a, b, check_exact=True)
        assert a.attrs == b.attrs and a is not b
    first[0].iloc[0] = -1.0                                              # a caller's edit does not reach the kept copy
    first[0].attrs["reason"] = "edited"
    third = h.daily_pnl_series_for_position(*calls[0], as_of=D9)
    assert third.iloc[0] == 100.0 and third.attrs["reason"] == ""
    assert h.constant_maturity_changes("CBOT:ZC", 2, raw=True).tolist() == [1.0, 3.0, 7.0]
    assert len(builds) == n_builds                                       # the #2 frame was kept too
    w1 = h.window_move_detail("CBOT:ZC", 1, D5, D9)
    w1["move"] = None
    assert h.window_move_detail("CBOT:ZC", 1, D5, D9)["move"] == pytest.approx(418 / 410 - 1)
    assert len(windows) == 1
    assert load_commodity_history(rv) is h                               # the same history object serves the next call


def test_a_changed_database_is_read_afresh(rv):
    before = load_commodity_history(rv).daily_pnl_series_for_position("CBOT:ZC", "C K26 Comdty", 1, 50, "USD", as_of=D9)
    writer = sqlite3.connect(rv)
    try:
        writer.execute("UPDATE price_daily SET settle = 500 WHERE contract_id = 'C K26 Comdty' AND date = ?", (D9,))
        writer.commit()
    finally:
        writer.close()
    after = load_commodity_history(rv).daily_pnl_series_for_position("CBOT:ZC", "C K26 Comdty", 1, 50, "USD", as_of=D9)
    assert before.loc[pd.Timestamp(D9)] == -100.0 and after.loc[pd.Timestamp(D9)] == 4000.0


# --------------------------------------------------------------------------------------
# research_curve: the research app's futures curve, context only
# --------------------------------------------------------------------------------------

CURVE_KEYS = {"root_id", "research_root", "available", "label", "path", "candidates", "as_of", "date", "stale_days",
              "unit", "currency", "price_scale", "reason", "note", "source", "rows"}
ROW_KEYS = {"contract_id", "month", "year", "month_no", "expiry", "settle", "raw_settle"}


def test_research_curve_on_a_settlement_day(rv):
    c = ch_mod.research_curve("CBOT:ZC", D6, db_path=rv)
    assert set(c) == CURVE_KEYS and c["reason"] == "" and c["available"] and c["label"] == "research"
    assert (c["research_root"], c["as_of"], c["date"], c["stale_days"]) == ("CBOT:ZC", D6, D6, 0)
    assert (c["unit"], c["currency"], c["price_scale"], c["path"]) == ("USD/t", "USD", 0.01, str(rv))
    assert [set(r) for r in c["rows"]] == [ROW_KEYS, ROW_KEYS]
    assert [(r["contract_id"], r["month"], r["expiry"], r["raw_settle"]) for r in c["rows"]] == [
        ("C H26 Comdty", "2026-03", D7, 402.0), ("C K26 Comdty", "2026-05", "2026-02-06", 411.0)]
    assert [r["settle"] for r in c["rows"]] == pytest.approx([4.02, 4.11])      # quote units: raw x price_scale
    assert (c["rows"][0]["year"], c["rows"][0]["month_no"]) == (2026, 3)
    assert c["note"] == ("2 of the 4 contracts listed on 2026-01-06 have no settlement that day, "
                         "the research app keeps the nearest 12")
    assert c["source"] == (f"research app database {rv}, CBOT:ZC settlements of {D6} (latest on or before {D6}), "
                           "in USD/t")
    json.dumps(c)


def test_research_curve_takes_the_latest_date_on_or_before_the_as_of(rv):
    c = ch_mod.research_curve("CBOT:ZC", "2026-01-10", db_path=rv)              # a Saturday
    assert (c["date"], c["stale_days"]) == (D9, 1)
    assert [(r["month"], r["raw_settle"]) for r in c["rows"]] == [("2026-05", 418.0), ("2026-07", 440.0)]
    assert c["note"].endswith(f"settlements of {D9}, 1 day(s) before 2026-01-10")
    assert ch_mod.research_curve("CBOT:ZC", pd.Timestamp(D7), db_path=rv)["date"] == D7
    assert ch_mod.research_curve("CBOT:ZC", None, db_path=rv)["date"] == D9     # None: the database's last date
    # our root id where the research app keeps its placeholder Bloomberg root
    wr = ch_mod.research_curve("SHFE:WR", D9, db_path=rv)
    assert (wr["research_root"], wr["date"], wr["unit"]) == ("SHFE:WR", D6, "CNY/t")
    assert [(r["contract_id"], r["month"], r["settle"]) for r in wr["rows"]] == [("ZZWRF27 Comdty", "2027-01", 101.0)]


def test_research_curve_reasons_never_exceptions(rv, tmp_path):
    absent = tmp_path / "none.sqlite"
    c = ch_mod.research_curve("CBOT:ZC", D9, db_path=absent)
    assert (c["available"], c["rows"], c["reason"]) == (False, [], f"no commodity history database: tried {absent}")
    assert c["candidates"] == [{"path": str(absent), "exists": False}]
    json.dumps(c)
    assert ch_mod.research_curve("NYMEX:CL", D9, db_path=rv)["reason"] == \
        f"root NYMEX:CL is not in the research database ({rv})"
    early = ch_mod.research_curve("CBOT:ZC", "2026-01-02", db_path=rv)
    assert (early["rows"], early["date"]) == ([], None)
    assert early["reason"] == \
        f"root CBOT:ZC has no settlement on or before 2026-01-02 in the research database (it starts {D5})"
    assert ch_mod.research_curve("CBOT:ZC", "not a date", db_path=rv)["reason"].startswith(
        "as-of 'not a date' is not a date")
    plain = tmp_path / "other.sqlite"
    sqlite3.connect(plain).execute("CREATE TABLE t (x)").connection.close()
    assert ch_mod.research_curve("CBOT:ZC", D9, db_path=plain)["reason"] == \
        f"{plain} is not the research app's database (no instrument, contract, price_daily, fx_daily table)"


def test_research_curve_reads_only(rv):
    before = (_digest(rv), rv.stat().st_mtime_ns)
    ch_mod.research_curve("CBOT:ZC", D9, db_path=rv)
    assert (_digest(rv), rv.stat().st_mtime_ns) == before
    writer = sqlite3.connect(rv, timeout=0)
    try:
        writer.execute("BEGIN EXCLUSIVE")
        writer.rollback()
    finally:
        writer.close()
