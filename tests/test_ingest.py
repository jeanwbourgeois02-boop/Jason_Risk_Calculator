"""Tests for data/ingest/schema.py (the SQLite schema + marks_official/trades_official
views + migrations).

The BNP CSV parser (data/ingest/bnp.py) and everything that tested it directly (parser
helper regexes, synthetic/real-file parsing, on_duplicate='skip' semantics, the
data/load.py CLI, swap packaging built on top of a BNP-shaped fixture, theme
inheritance on bnp.load) were removed 2026-09-17 along with the module itself ("no bnp
fall back", docs/bnp-excel-removal.md). What moved rather than disappeared:
  - shared dataclasses/regexes/helpers that data/ingest/blotter.py (the live parser)
    depends on -> data/ingest/common.py, tests -> tests/test_ingest_common.py
  - data/ingest/swaps.py's own edge-case coverage (round trips, ambiguous candidates,
    idempotency, cross-source exclusion, crosses with no USD leg), rebuilt on direct SQL
    fixtures instead of bnp.load -> tests/test_swaps.py
  - blotter-specific loading/parsing coverage already lives in tests/test_blotter.py
Theme inheritance on load (bnp.load re-tagging an existing untagged trade in a pair when
a new trade in that pair is tagged) had no equivalent in data/ingest/blotter.py's own
load() (which does not do inheritance at all) -- that BNP-only behaviour has no live
successor to test and was not carried forward.
"""
from __future__ import annotations

import sqlite3

import pytest

from data.ingest import schema


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
                              "quantity", "price", "account", "counterparty", "strategy", "trader", "description",
                              "theme"]
    assert cols("instrument_theme") == ["instrument_id", "theme"]
    assert cols("trade_legs") == ["trade_id", "leg_no", "leg_type", "ccy", "amount", "start_date", "settle_date",
                                  "rate", "settles_cash"]
    assert cols("marks") == ["as_of_date", "instrument_id", "settle_date", "mark_type", "value", "source",
                             "snapped_at"]
    assert cols("curves") == ["curve_id", "as_of_date", "node_date", "discount_factor", "par_rate", "source"]
    # every column NOT NULL
    for t in schema.TABLES:
        for r in conn.execute(f"PRAGMA table_info({t})"):
            assert r[3] == 1 or r[5] == 1, f"{t}.{r[1]} is nullable"


def test_schema_has_curve_quotes_table():
    conn = schema.connect()
    assert "curve_quotes" in schema.TABLES
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "curve_quotes" in names
    cols = [r[1] for r in conn.execute("PRAGMA table_info(curve_quotes)")]
    assert cols == ["as_of_date", "ccy", "index", "tenor", "ticker", "value", "quote_type", "field", "source"]
    for r in conn.execute("PRAGMA table_info(curve_quotes)"):
        assert r[3] == 1, f"curve_quotes.{r[1]} is nullable"
    # (as_of_date, ccy, index, tenor, source) is the PK -> distinct tickers/fields collide
    conn.execute(
        "INSERT INTO curve_quotes VALUES ('2026-08-17','USD','SOFR','1Y','USSO1 Curncy',3.5,'PAR','PX_LAST','BBG_BDP')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO curve_quotes VALUES ('2026-08-17','USD','SOFR','1Y','USSO1 Curncy',3.6,'PAR','PX_LAST','BBG_BDP')")


def test_official_mark_source_uses_ql_pricer_for_irs_marks():
    assert schema.OFFICIAL_MARK_SOURCE["PAR_RATE"] == "QL_PRICER"
    assert schema.OFFICIAL_MARK_SOURCE["PV_USD"] == "QL_PRICER"
    assert schema.OFFICIAL_MARK_SOURCE["DV01_USD"] == "QL_PRICER"
    # unrelated mark_types are unchanged
    assert schema.OFFICIAL_MARK_SOURCE["SPOT"] == "BBG_BFXFORWARD"
    assert schema.OFFICIAL_MARK_SOURCE["FWD_OUTRIGHT"] == "BBG_BFXFORWARD"
    assert schema.OFFICIAL_MARK_SOURCE["FUTURE_PX"] == "BBG_BDH"
    # DELTA / PREMIUM moved to the engine/options pricer 2026-09-17; MANUAL is
    # reconciliation-only for them now.
    assert schema.OFFICIAL_MARK_SOURCE["DELTA"] == "QL_OPTIONS_PRICER"
    assert schema.OFFICIAL_MARK_SOURCE["PREMIUM"] == "QL_OPTIONS_PRICER"


def test_marks_official_prefers_ql_pricer_over_bbg_bdh_for_pv_usd():
    conn = schema.connect()
    conn.execute("INSERT INTO instruments VALUES "
                 "('IRSOIS-USD-1','IRS','USD','USD',1,0,'IRSOIS-USD-1','2027-02-11')")
    rows = [
        ("2026-08-17", "IRSOIS-USD-1", "2027-02-11", "PV_USD", 100.0, "BBG_BDH", "2026-08-17T17:00:00-04:00"),
        ("2026-08-17", "IRSOIS-USD-1", "2027-02-11", "PV_USD", 101.0, "QL_PRICER", "2026-08-17T17:00:00-04:00"),
    ]
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", rows)
    got = conn.execute("SELECT value, source FROM marks_official WHERE mark_type='PV_USD'").fetchall()
    assert got == [(101.0, "QL_PRICER")]


def test_schema_foreign_keys_enforced():
    conn = schema.connect()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO trades VALUES ('t1','MANUAL','NOPE','FX_FWD','t1','2026-08-17',1,1,'a','c','s','tr','d','')")


def test_marks_official_filters_to_official_source():
    conn = schema.connect()
    conn.execute("INSERT INTO instruments VALUES ('USDJPY','FX','USD','JPY',1,0,'USDJPY Curncy','9999-12-31')")
    rows = [
        ("2026-08-17", "USDJPY", "2026-08-17", "SPOT", 147.10, "BNP_BVAL", "2026-08-17T15:00:00-04:00"),
        ("2026-08-17", "USDJPY", "2026-08-17", "SPOT", 147.12, "BBG_BFXFORWARD", "2026-08-17T15:00:00-04:00"),
        ("2026-08-17", "USDJPY", "2026-08-17", "DELTA", 0.5, "BBG_BFXFORWARD", "2026-08-17T15:00:00-04:00"),
        ("2026-08-17", "USDJPY", "2026-08-17", "DELTA", 0.6, "MANUAL", "2026-08-17T15:00:00-04:00"),
        ("2026-08-17", "USDJPY", "2026-08-17", "DELTA", 0.55, "QL_OPTIONS_PRICER", "2026-08-17T15:00:00-04:00"),
        ("2026-08-17", "USDJPY", "2026-08-17", "PV_USD", 1.0, "BNP_BVAL", "2026-08-17T15:00:00-04:00"),
    ]
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", rows)
    assert conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0] == 6
    got = conn.execute(
        "SELECT mark_type, value, source FROM marks_official ORDER BY mark_type").fetchall()
    assert got == [("DELTA", 0.55, "QL_OPTIONS_PRICER"), ("SPOT", 147.12, "BBG_BFXFORWARD")]


# --------------------------------------------------------------------------- BUILD_PLAN task B
def test_migration_adds_theme_and_realised_pnl_columns_to_an_existing_db(tmp_path):
    """A database created before this change (no theme / instrument_theme / product /
    mark_type) is upgraded in place by connect(), never dropped or recreated."""
    db_path = tmp_path / "old.db"
    old = sqlite3.connect(str(db_path))
    old.executescript("""
        CREATE TABLE instruments (instrument_id TEXT PRIMARY KEY, asset_class TEXT NOT NULL,
          base_ccy TEXT NOT NULL, quote_ccy TEXT NOT NULL, multiplier REAL NOT NULL,
          is_ndf INTEGER NOT NULL, bbg_ticker TEXT NOT NULL, expiry_date TEXT NOT NULL);
        CREATE TABLE trades (trade_id TEXT PRIMARY KEY, source TEXT NOT NULL,
          instrument_id TEXT NOT NULL, product TEXT NOT NULL, package_id TEXT NOT NULL,
          trade_date TEXT NOT NULL, quantity REAL NOT NULL, price REAL NOT NULL,
          account TEXT NOT NULL, counterparty TEXT NOT NULL, strategy TEXT NOT NULL,
          trader TEXT NOT NULL, description TEXT NOT NULL);
        CREATE TABLE realised_pnl (trade_id TEXT PRIMARY KEY, instrument_id TEXT NOT NULL,
          currency TEXT NOT NULL, settle_date TEXT NOT NULL, local_amount REAL NOT NULL,
          usd_entry_amount REAL NOT NULL, spot_usd_per_local REAL NOT NULL,
          spot_as_of_date TEXT NOT NULL, spot_source TEXT NOT NULL, pnl_usd REAL NOT NULL,
          frozen_at TEXT NOT NULL, note TEXT NOT NULL);
        INSERT INTO trades VALUES ('t1','MANUAL','X','FX_FWD','t1','2026-08-17',1,1,'a','c','s','tr','d');
    """)
    old.commit()
    old.close()

    conn = schema.connect(db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(trades)")]
    assert cols[-1] == "theme"
    assert conn.execute("SELECT theme FROM trades WHERE trade_id='t1'").fetchone()[0] == ""
    assert "instrument_theme" in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    rp_cols = [r[1] for r in conn.execute("PRAGMA table_info(realised_pnl)")]
    assert "product" in rp_cols and "mark_type" in rp_cols
    # idempotent: migrating twice does not error
    schema.create_schema(conn)
    conn.close()


def test_ledger_tables_no_longer_include_pnl_snapshots():
    assert schema.LEDGER_TABLES == ("realised_pnl",)
    conn = schema.connect()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "pnl_snapshots" not in tables


def test_create_schema_resyncs_a_stale_marks_official_view(tmp_path):
    """`CREATE VIEW IF NOT EXISTS` used to leave a pre-existing (older-definition)
    marks_official view untouched forever on databases created before a view-definition
    change -- e.g. a pre-2026-09-15 database's marks_official had no branch for
    CASHFLOW_USD/GAMMA/THETA/VEGA/RHO/DELTA_PA and routed PV_USD to BBG_BDH and DELTA to
    MANUAL, both now wrong (schema.py::_views_ddl's docstring). `create_schema` now
    drops and recreates every view unconditionally, so re-running it on such a database
    re-syncs the view to the current OFFICIAL_MARK_SOURCE mapping."""
    path = tmp_path / "old.db"
    old = sqlite3.connect(str(path))
    old.executescript("""
        CREATE TABLE marks (as_of_date TEXT, instrument_id TEXT, settle_date TEXT,
          mark_type TEXT, value REAL, source TEXT, snapped_at TEXT,
          PRIMARY KEY (as_of_date, instrument_id, settle_date, mark_type, source));
        CREATE VIEW marks_official AS
        SELECT * FROM marks WHERE source = CASE mark_type
            WHEN 'PV_USD' THEN 'BBG_BDH'
            WHEN 'DELTA' THEN 'MANUAL'
            ELSE 'NONE-SUCH'
        END;
    """)
    old.commit()
    old.close()

    conn = schema.connect(path)
    try:
        conn.execute("INSERT INTO instruments VALUES ('IRSOIS-USD-1','IRS','USD','USD',1,0,'IRSOIS-USD-1','2027-02-11')")
        conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", [
            ("2026-08-17", "IRSOIS-USD-1", "2027-02-11", "PV_USD", 100.0, "BBG_BDH", "t"),
            ("2026-08-17", "IRSOIS-USD-1", "2027-02-11", "PV_USD", 101.0, "QL_PRICER", "t"),
        ])
        got = conn.execute("SELECT value, source FROM marks_official WHERE mark_type='PV_USD'").fetchall()
        assert got == [(101.0, "QL_PRICER")]  # re-synced view routes PV_USD to QL_PRICER, not BBG_BDH
    finally:
        conn.close()


def test_positions_table_no_longer_exists():
    """2026-09-17 ("no bnp fall back"): `positions` was written only by the retired BNP
    parser; the table itself is dropped from the schema, not just left empty."""
    conn = schema.connect()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "positions" not in tables
    assert "positions" not in schema.TABLES


# --------------------------------------------------------------------------- purge_retired_sources
def _seed_instrument(conn, instrument_id="USDJPY", base="USD", quote="JPY"):
    conn.execute(
        "INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)",
        (instrument_id, "FX", base, quote, 1, 0, f"{instrument_id} Curncy", "9999-12-31"),
    )


def test_purge_retired_sources_on_a_scratch_db_with_legacy_bnp_data(tmp_path):
    """Run on a scratch DB shaped like a pre-2026-09-17 live database: a legacy BNP
    trade (with legs and a realised_pnl row), some BNP_BVAL/BBG_INTERP/WORKBOOK_REFERENCE
    marks, live XLSX data that must survive untouched, and an old-style `positions`
    table. Idempotent: a second run is a no-op with all-zero counts."""
    db_path = tmp_path / "scratch.db"
    conn = schema.connect(db_path)
    _seed_instrument(conn)
    conn.execute(
        "INSERT INTO trades VALUES ('bnp-1','BNP','USDJPY','FX_FWD','bnp-1','2026-08-01',"
        "1000000,147.0,'acc','cp','HAHY7','t','d','')"
    )
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("bnp-1", 1, "FX_NEAR", "USD", 1000000, "2026-08-01", "2026-09-01", 147.0, 1),
        ("bnp-1", 2, "FX_NEAR", "JPY", -147000000, "2026-08-01", "2026-09-01", 147.0, 1),
    ])
    conn.execute(
        "INSERT INTO realised_pnl VALUES ('bnp-1','USDJPY','FX_FWD','JPY','2026-09-01',"
        "-147000000,1000000,'SPOT',150.0,'2026-09-01','BBG_BFXFORWARD',20000.0,'t','')"
    )
    conn.execute(
        "INSERT INTO trades VALUES ('xlsx-1','XLSX','USDJPY','FX_FWD','xlsx-1','2026-08-01',"
        "500000,147.0,'acc','cp','HAHY7','t','d','')"
    )
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("xlsx-1", 1, "FX_NEAR", "USD", 500000, "2026-08-01", "2026-09-01", 147.0, 1),
        ("xlsx-1", 2, "FX_NEAR", "JPY", -73500000, "2026-08-01", "2026-09-01", 147.0, 1),
    ])
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", [
        ("2026-08-17", "USDJPY", "2026-08-17", "SPOT", 147.10, "BNP_BVAL", "t"),
        ("2026-08-17", "USDJPY", "2026-09-01", "FWD_OUTRIGHT", 148.0, "BBG_INTERP", "t"),
        ("2026-08-17", "USDJPY", "2026-08-24", "FWD_OUTRIGHT", 149.0, "WORKBOOK_REFERENCE", "t"),
        ("2026-08-17", "USDJPY", "2026-08-17", "SPOT", 147.12, "BBG_BFXFORWARD", "t"),
    ])
    conn.execute(
        "CREATE TABLE IF NOT EXISTS positions (as_of_date TEXT, source TEXT, account TEXT, "
        "instrument_id TEXT, settle_date TEXT, quantity REAL, cost_local REAL, mark REAL, "
        "fx_to_usd REAL, mv_local REAL, mv_usd REAL, pnl_dtd_usd REAL, pnl_mtd_usd REAL, pnl_ytd_usd REAL)"
    )
    conn.commit()

    counts = schema.purge_retired_sources(conn)
    assert counts["trades"] == 1 and counts["trade_legs"] == 2 and counts["realised_pnl"] == 1
    # BNP_BVAL + WORKBOOK_REFERENCE only: the BBG_INTERP forward is the official FWD_OUTRIGHT
    # fallback since 2026-09-18 and must survive every startup purge
    assert counts["marks"] == 2 and counts["positions_table_dropped"] == 1
    assert conn.execute("SELECT COUNT(*) FROM marks WHERE source='BBG_INTERP'").fetchone()[0] == 1

    assert conn.execute("SELECT trade_id FROM trades").fetchall() == [("xlsx-1",)]
    assert conn.execute("SELECT COUNT(*) FROM trade_legs").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM realised_pnl").fetchone()[0] == 0
    remaining_marks = {r[0] for r in conn.execute("SELECT source FROM marks")}
    assert remaining_marks == {"BBG_BFXFORWARD", "BBG_INTERP"}   # the interpolated forward survives
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "positions" not in tables

    # idempotent: nothing left to purge on a second run
    second = schema.purge_retired_sources(conn)
    assert all(v == 0 for v in second.values())


def test_marks_official_falls_back_to_interpolated_forward_only_where_no_direct_quote():
    """User decision 2026-09-18 (CLAUDE.md "Official marks"): BBG_INTERP is the official
    FWD_OUTRIGHT fallback where no BBG_BFXFORWARD row exists for the same key; a direct
    quote still wins over it; SPOT (and every other mark_type) never falls back."""
    conn = schema.connect()
    _seed_instrument(conn)
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", [
        ("2026-09-17", "USDJPY", "2026-09-24", "FWD_OUTRIGHT", 147.5, "BBG_INTERP", "t"),      # broken date: fallback
        ("2026-09-17", "USDJPY", "2026-10-19", "FWD_OUTRIGHT", 147.2, "BBG_INTERP", "t"),      # both exist: direct wins
        ("2026-09-17", "USDJPY", "2026-10-19", "FWD_OUTRIGHT", 147.3, "BBG_BFXFORWARD", "t"),
        ("2026-09-17", "USDJPY", "2026-09-17", "SPOT", 147.9, "BBG_INTERP", "t"),               # never official
    ])
    conn.commit()
    rows = {(r[0], r[1]): (r[2], r[3]) for r in conn.execute(
        "SELECT mark_type, settle_date, value, source FROM marks_official WHERE instrument_id='USDJPY'")}
    assert rows[("FWD_OUTRIGHT", "2026-09-24")] == (147.5, "BBG_INTERP")
    assert rows[("FWD_OUTRIGHT", "2026-10-19")] == (147.3, "BBG_BFXFORWARD")
    assert ("SPOT", "2026-09-17") not in rows
    assert conn.execute("SELECT COUNT(*) FROM marks_official WHERE instrument_id='USDJPY'").fetchone()[0] == 2
    assert schema.OFFICIAL_FALLBACK_SOURCE == {"FWD_OUTRIGHT": "BBG_INTERP"}
    assert schema.OFFICIAL_MARK_SOURCE["FWD_OUTRIGHT"] == "BBG_BFXFORWARD"
