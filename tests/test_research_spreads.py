"""engine/risk/research_spreads.py: the research app's spread statistics, read-only, on a small
fixture database shaped like `../Commodity Dashboard/rvapp/db/schema.sql` (never the real file)."""
import hashlib
import sqlite3

import pandas as pd
import pytest

from engine.risk import research_spreads as rs

DEF_COLS = "spread_id, family, name, sector, legs, unit, verified, source, in_universe, loaded_at"


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE spread_def (spread_id TEXT PRIMARY KEY, family TEXT NOT NULL, name TEXT NOT NULL,
            sector TEXT NOT NULL, legs TEXT NOT NULL, unit TEXT NOT NULL, fx TEXT NOT NULL DEFAULT '[]',
            tenor TEXT NOT NULL DEFAULT 'main', verified INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL,
            cal_instrument_id TEXT, near_month TEXT, far_month TEXT, far_year_offset INTEGER,
            in_universe INTEGER NOT NULL DEFAULT 1, loaded_at TEXT NOT NULL);
        CREATE TABLE spread_daily (spread_id TEXT NOT NULL, instance TEXT NOT NULL DEFAULT '', date TEXT NOT NULL,
            value REAL NOT NULL, ratio REAL, legs TEXT NOT NULL DEFAULT '[]', PRIMARY KEY (spread_id, instance, date));
        CREATE TABLE spread_stats (spread_id TEXT NOT NULL, instance TEXT NOT NULL DEFAULT '', asof TEXT NOT NULL,
            computed_at TEXT NOT NULL, heavy_asof TEXT, last_obs_date TEXT, stale_days INTEGER, stale_leg TEXT,
            contract_note TEXT NOT NULL DEFAULT '', n_obs INTEGER, level REAL, ratio REAL, chg_1d REAL,
            chg_1d_sd REAL, z_1y REAL, pctile_5y REAL, z_primary REAL, z_primary_kind TEXT, dvol_20d REAL,
            half_life_days REAL, PRIMARY KEY (spread_id, instance, asof));
    """)
    defs = [
        ("bench.crude.brent_vs_wti", "benchmark", "Brent vs WTI", "energy", "[]", "USD/bbl", 0, "yaml", 1, "x"),
        ("cal.nymex_cl.z_f", "calendar", "WTI Z/F", "energy", "[]", "USD/bbl", 1, "calendar_rule", 1, "x"),
        ("cal.nymex_cl.x_z", "calendar", "WTI X/Z", "energy", "[]", "USD/bbl", 1, "calendar_rule", 1, "x"),
        ("proc.us.crack_321", "processing", "US 3-2-1 crack", "energy", "[]", "USD/bbl", 0, "yaml", 1, "x"),
        ("sub.old.gone", "substitution", "Retired", "energy", "[]", "USD/t", 0, "yaml", 0, "x"),
        ("bench.no.stats", "benchmark", "No stats yet", "energy", "[]", "USD/t", 0, "yaml", 1, "x"),
    ]
    conn.executemany(f"INSERT INTO spread_def ({DEF_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?)", defs)
    cols = ("spread_id, instance, asof, computed_at, heavy_asof, last_obs_date, stale_days, stale_leg, n_obs, "
            "level, chg_1d, chg_1d_sd, z_1y, pctile_5y, z_primary, z_primary_kind, dvol_20d, half_life_days")
    stats = [
        ("bench.crude.brent_vs_wti", "", "2026-09-21", "t1", "2026-09-21", "2026-09-21", 0, None, 1500,
         0.47, 0.1, 0.2, 0.9, 60.0, 0.9, "z_1y", 0.70, 12.0),
        ("bench.crude.brent_vs_wti", "", "2026-09-22", "t2", "2026-09-22", "2026-09-22", 0, None, 1501,
         0.86, 0.39, 0.65, 1.17, 69.4, 1.17, "z_1y", 0.732, 12.7),
        ("cal.nymex_cl.z_f", "2026", "2026-09-22", "t2", "2026-09-22", "2026-09-22", 0, None, 296,
         -1.24, -0.22, -1.02, -2.0, 7.8, -3.23, "seasonal_analogue", 0.2448, None),
        ("cal.nymex_cl.x_z", "2026", "2026-09-21", "t1", "2026-09-21", "2026-09-21", 1, "NYMEX:CL", 290,
         -0.5, 0.0, 0.0, 0.1, 50.0, 0.1, "seasonal_analogue", None, None),
        ("sub.old.gone", "", "2026-09-22", "t2", None, "2026-09-18", 2, None, 10,
         3.0, None, None, None, None, None, None, 1.5, None),
    ]
    conn.executemany(f"INSERT INTO spread_stats ({cols}) VALUES ({','.join('?' * 18)})", stats)
    daily = [("cal.nymex_cl.z_f", "2026", d, v) for d, v in
             [("2026-09-17", -1.0), ("2026-09-18", -1.1), ("2026-09-21", -1.02), ("2026-09-22", -1.24)]]
    daily += [("cal.nymex_cl.z_f", "2025", "2025-09-22", 0.5)]
    conn.executemany("INSERT INTO spread_daily (spread_id, instance, date, value) VALUES (?,?,?,?)", daily)
    conn.commit()
    conn.close()


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "rv.sqlite"
    _make_db(p)
    return p


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------------------ stats
def test_template_key_reads_latest_run_on_or_before_as_of(db):
    key = ("bench.crude.brent_vs_wti", "")
    out = rs.research_spread_stats([key], "2026-09-22", db_path=db)
    assert out["available"] and out["reason"] == "" and out["path"] == str(db)
    assert out["run_asof"] == "2026-09-22" and out["label"] == "research"
    assert str(db) in out["source"] and "2026-09-22" in out["source"]
    e = out["stats"][key]
    assert e["found"] and e["reason"] == "" and e["note"] == ""
    assert e["asof"] == "2026-09-22" and e["level"] == pytest.approx(0.86) and e["dvol_20d"] == pytest.approx(0.732)
    assert e["z_primary_kind"] == "z_1y" and e["pctile_5y"] == pytest.approx(69.4)
    assert e["half_life_days"] == pytest.approx(12.7) and e["chg_1d_sd"] == pytest.approx(0.65)
    assert (e["name"], e["family"], e["unit"], e["verified"]) == ("Brent vs WTI", "benchmark", "USD/bbl", False)
    assert e["stale_days"] == 0 and e["last_obs_date"] == "2026-09-22"
    # A past as-of reads its own run, never a later one.
    past = rs.research_spread_stats([key], pd.Timestamp("2026-09-21"), db_path=db)
    assert past["run_asof"] == "2026-09-21" and past["stats"][key]["level"] == pytest.approx(0.47)


def test_calendar_key_normalised_and_keyed_as_given(db):
    key = ("CAL.NYMEX_CL.Z_F", 2026)          # an int near year, upper case: as a caller might give it
    e = rs.research_spread_stats([key], "2026-09-25", db_path=db)["stats"][key]
    assert e["found"] and (e["spread_id"], e["instance"]) == ("cal.nymex_cl.z_f", "2026")
    assert e["verified"] is True and e["half_life_days"] is None      # NULL = not enough history
    assert e["z_primary"] == pytest.approx(-3.23)
    key2 = ("bench.crude.brent_vs_wti", None)  # None instance = ''
    assert rs.research_spread_stats([key2], "2026-09-25", db_path=db)["stats"][key2]["found"]


def test_reasons_not_in_universe_untracked_instance_before_first_run(db):
    keys = [("cal.nymex_cl.z_h", "2026"), ("cal.nymex_cl.z_f", "2027"), ("bench.crude.brent_vs_wti", ""),
            ("bench.no.stats", ""), ("no.such.thing", "")]
    s = rs.research_spread_stats(keys, "2026-09-20", db_path=db)["stats"]
    assert not s[keys[0]]["found"] and "not in the research universe" in s[keys[0]]["reason"]
    assert "x_z, z_f" in s[keys[0]]["reason"]                      # the root's calendars named
    assert "tracks instance 2026" in s[keys[1]]["reason"]
    assert "on or before 2026-09-20" in s[keys[2]]["reason"] and "2026-09-21" in s[keys[2]]["reason"]
    assert s[keys[2]]["name"] == "Brent vs WTI"                   # the definition still served
    assert "computed no statistics" in s[keys[3]]["reason"]
    assert "not in the research universe" in s[keys[4]]["reason"] and s[keys[4]]["level"] is None


def test_older_run_and_retired_spread_are_noted(db):
    keys = [("cal.nymex_cl.x_z", "2026"), ("sub.old.gone", "")]
    s = rs.research_spread_stats(keys, "2026-09-22", db_path=db)["stats"]
    assert s[keys[0]]["found"] and s[keys[0]]["asof"] == "2026-09-21"
    assert "not in the 2026-09-22 run" in s[keys[0]]["note"]
    assert s[keys[1]]["found"] and "no longer in the research universe" in s[keys[1]]["note"]


def test_no_database_names_paths_tried(tmp_path, monkeypatch):
    missing = tmp_path / "nowhere" / "rv.sqlite"
    key = ("bench.crude.brent_vs_wti", "")
    out = rs.research_spread_stats([key], "2026-09-22", db_path=missing)
    assert not out["available"] and str(missing) in out["reason"] and out["candidates"][0]["exists"] is False
    assert not out["stats"][key]["found"] and str(missing) in out["stats"][key]["reason"]
    monkeypatch.setenv("COMMODITY_HISTORY_DB", str(missing))     # the env var is honoured, like commodity_history
    out = rs.research_spread_stats([key], "2026-09-22")
    assert not out["available"] and str(missing) in out["reason"]


def test_env_var_finds_the_database(db, monkeypatch):
    monkeypatch.setenv("COMMODITY_HISTORY_DB", str(db))
    out = rs.research_spread_stats([("bench.crude.brent_vs_wti", "")], "2026-09-22")
    assert out["available"] and out["path"] == str(db)


def test_foreign_database_and_bad_inputs(tmp_path, db):
    other = tmp_path / "other.sqlite"
    sqlite3.connect(other).execute("CREATE TABLE marks (x)").connection.close()
    key = ("bench.crude.brent_vs_wti", "")
    out = rs.research_spread_stats([key], "2026-09-22", db_path=other)
    assert not out["available"] and "not the research app's database" in out["reason"]
    out = rs.research_spread_stats([key], "not a date", db_path=db)
    assert "not a date" in out["stats"][key]["reason"]
    out = rs.research_spread_stats(["bench.crude.brent_vs_wti"], "2026-09-22", db_path=db)
    assert "not a (spread_id, instance) pair" in out["stats"]["bench.crude.brent_vs_wti"]["reason"]
    assert rs.research_spread_stats([], "2026-09-22", db_path=db)["stats"] == {}


def test_reads_never_write_the_research_database(db):
    before = _digest(db)
    rs.research_spread_stats([("bench.crude.brent_vs_wti", ""), ("x", "")], "2026-09-22", db_path=db)
    rs.research_spread_history(("cal.nymex_cl.z_f", "2026"), db_path=db)
    assert _digest(db) == before


# ------------------------------------------------------------------------ sigma
def test_sigma_move(db):
    s = rs.research_spread_stats([("cal.nymex_cl.z_f", "2026"), ("cal.nymex_cl.x_z", "2026"), ("zz", "")],
                                 "2026-09-22", db_path=db)["stats"]
    zf = s[("cal.nymex_cl.z_f", "2026")]
    val, why = rs.sigma_move(-0.4896, zf)
    assert why == "" and val == pytest.approx(-2.0)
    assert rs.sigma_move(-0.4896, zf, unit="$/bbl")[0] == pytest.approx(-2.0)
    assert rs.sigma_move(-0.4896, zf, unit=" usd/BBL ")[0] == pytest.approx(-2.0)
    val, why = rs.sigma_move(1.0, zf, unit="USD/t")
    assert val is None and "units differ" in why and "USD/bbl" in why
    val, why = rs.sigma_move(1.0, s[("cal.nymex_cl.x_z", "2026")])
    assert val is None and "20-day daily vol" in why
    val, why = rs.sigma_move(1.0, s[("zz", "")])
    assert val is None and "not in the research universe" in why
    for bad in (None, float("nan"), "abc"):
        val, why = rs.sigma_move(bad, zf)
        assert val is None and "no level move" in why
    assert rs.sigma_move(1.0, None)[0] is None


# ---------------------------------------------------------------------- history
def test_history_window_and_instance(db):
    h = rs.research_spread_history(("cal.nymex_cl.z_f", 2026), "2026-09-18", "2026-09-21", db_path=db)
    assert list(h.index.strftime("%Y-%m-%d")) == ["2026-09-18", "2026-09-21"]
    assert list(h) == pytest.approx([-1.1, -1.02]) and h.index.name == "date"
    assert h.attrs["reason"] == "" and h.attrs["unit"] == "USD/bbl" and h.attrs["label"] == "research"
    full = rs.research_spread_history(("cal.nymex_cl.z_f", "2026"), db_path=db)
    assert len(full) == 4                                           # the 2025 instance kept apart
    assert len(rs.research_spread_history(("cal.nymex_cl.z_f", "2025"), db_path=db)) == 1


def test_history_reasons(tmp_path, db):
    h = rs.research_spread_history(("cal.nymex_cl.z_f", "2026"), "2027-01-01", None, db_path=db)
    assert h.empty and "no history" in h.attrs["reason"] and h.attrs["unit"] == "USD/bbl"
    h = rs.research_spread_history(("nope", ""), db_path=db)
    assert h.empty and "not in the research universe" in h.attrs["reason"]
    h = rs.research_spread_history(("cal.nymex_cl.z_f", "2026"), db_path=tmp_path / "missing.sqlite")
    assert h.empty and "missing.sqlite" in h.attrs["reason"]
    h = rs.research_spread_history(("cal.nymex_cl.z_f", "2026"), "bad date", db_path=db)
    assert h.empty and "not a date" in h.attrs["reason"]
