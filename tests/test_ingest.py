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


# --------------------------------------------------------------------------- irs_direction
# data/ingest/irs_direction.py: the user's own pay/receive direction per swap. The export
# carries no direction at all (Side = Buy and unsigned amounts on every swap row,
# receivers included), so this is the primary source of swap direction, not a fallback.
from data.ingest import irs_direction  # noqa: E402

SWAP = "IRSOIS-USD-22860996"


def _seed_swap(conn, trade_id="918421481", instrument_id=SWAP, quantity=625e6):
    conn.execute("INSERT OR IGNORE INTO instruments VALUES (?,?,?,?,?,?,?,?)",
                 (instrument_id, "IRS", "USD", "USD", 1, 0, instrument_id, "2027-02-11"))
    conn.execute("INSERT INTO trades VALUES (?,'XLSX',?,'IRS',?,'2026-08-07',?,0.0398,'GSCO-DRV-NMMF','HSBCUK','','HA','d','')",
                 (trade_id, instrument_id, trade_id, quantity))
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        (trade_id, 1, "FIXED", "USD", -quantity, "2026-11-11", "2027-02-11", 0.0398, 0),
        (trade_id, 2, "FLOAT", "USD", quantity, "2026-11-11", "2027-02-11", 0.0, 0)])
    conn.commit()


def _seed_marks(conn, instrument_id=SWAP):
    rows = [(d, instrument_id, "2027-02-11", mt, 1.0, src, "t")
            for d in ("2026-09-16", "2026-09-17")
            for mt, src in (("PV_USD", "QL_PRICER"), ("DV01_USD", "QL_PRICER"), ("PAR_RATE", "QL_PRICER"),
                            ("CASHFLOW_USD", "QL_PRICER"), ("PV_USD", "BBG_BDH"))]
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()


def _swap_state(conn, trade_id="918421481"):
    quantity = conn.execute("SELECT quantity FROM trades WHERE trade_id=?", (trade_id,)).fetchone()[0]
    return quantity, dict(conn.execute("SELECT leg_type, amount FROM trade_legs WHERE trade_id=?", (trade_id,)))


def test_schema_creates_the_irs_direction_overrides_table_with_the_agreed_shape():
    conn = schema.connect()
    assert schema.IRS_DIRECTION_TABLES == ("irs_direction_overrides",)
    cols = [(r[1], r[2], r[3], r[5]) for r in conn.execute("PRAGMA table_info(irs_direction_overrides)")]
    assert cols == [("trade_id", "TEXT", 0, 1), ("direction", "TEXT", 1, 0), ("set_at", "TEXT", 1, 0)]
    with pytest.raises(sqlite3.IntegrityError):                # CHECK(direction IN ('PAY','RECEIVE'))
        conn.execute("INSERT INTO irs_direction_overrides VALUES ('t','SIDEWAYS','2026-09-18')")
    # no foreign key to trades: an override must outlive the book being deleted and rewritten
    conn.execute("INSERT INTO irs_direction_overrides VALUES ('not-on-file','RECEIVE','2026-09-18')")
    assert list(conn.execute("PRAGMA foreign_key_list(irs_direction_overrides)")) == []


def _marks_of(conn, instrument_id=SWAP):
    return sorted(conn.execute("SELECT as_of_date, mark_type, source, value FROM marks WHERE instrument_id = ?",
                               (instrument_id,)).fetchall())


def test_set_direction_flips_trade_and_legs_stores_the_override_and_turns_the_priced_history_round():
    """A flip keeps the swap's history (swaps are priced for today only, so deleted marks
    never come back): the pricer's PV / DV01 / cashflow rows are multiplied by -1 on
    EVERY date, PAR_RATE is untouched, rows of those three types from any other source
    are deleted (nobody knows which direction they were entered for), and the realised
    row goes so the ledger re-realises it from the reversed marks."""
    conn = schema.connect()
    _seed_swap(conn)
    _seed_swap(conn, "929068982", "IRSOIS-USD-22907452", 403.1e6)          # a bystander
    _seed_marks(conn)
    _seed_marks(conn, "IRSOIS-USD-22907452")
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", [
        ("2026-09-17", SWAP, "2026-09-17", "SPOT", 1.0, "BBG_BFXFORWARD", "t"),      # not a swap mark
        ("2026-09-17", SWAP, "2027-02-11", "DV01_USD", 7.0, "MANUAL", "t"),          # other source: deleted
        ("2026-09-17", SWAP, "2027-02-11", "PAR_RATE", 0.04, "BBG_BDH", "t"),        # direction-free: kept
        ("2026-09-15", SWAP, "2027-02-11", "PV_USD", 0.0, "QL_PRICER", "t"),         # 0 stays 0.0, not -0.0
    ])
    conn.executemany("INSERT INTO realised_pnl VALUES (?,?,'IRS','USD','2027-02-11',5.0,0.0,'PV_USD',1.0,'2027-02-11','QL_PRICER',5.0,'t','')",
                     [("918421481", SWAP), ("929068982", "IRSOIS-USD-22907452")])
    conn.commit()
    bystander_before = _marks_of(conn, "IRSOIS-USD-22907452")

    irs_direction.set_direction(conn, "918421481", "RECEIVE")

    assert _swap_state(conn) == (-625e6, {"FIXED": 625e6, "FLOAT": -625e6})
    assert irs_direction.get_overrides(conn) == {"918421481": "RECEIVE"}
    set_at = conn.execute("SELECT set_at FROM irs_direction_overrides").fetchone()[0]
    assert set_at[:4].isdigit() and "T" in set_at
    assert _marks_of(conn) == sorted(
        [(d, mt, "QL_PRICER", -1.0) for d in ("2026-09-16", "2026-09-17") for mt in ("PV_USD", "DV01_USD", "CASHFLOW_USD")]
        + [(d, "PAR_RATE", "QL_PRICER", 1.0) for d in ("2026-09-16", "2026-09-17")]
        + [("2026-09-17", "PAR_RATE", "BBG_BDH", 0.04), ("2026-09-17", "SPOT", "BBG_BFXFORWARD", 1.0),
           ("2026-09-15", "PV_USD", "QL_PRICER", 0.0)])
    zero = conn.execute("SELECT value FROM marks WHERE as_of_date = '2026-09-15'").fetchone()[0]
    assert str(zero) == "0.0"                                              # never -0.0
    assert _marks_of(conn, "IRSOIS-USD-22907452") == bystander_before      # the other swap is untouched
    assert [r[0] for r in conn.execute("SELECT trade_id FROM realised_pnl")] == ["929068982"]
    assert _swap_state(conn, "929068982")[0] == 403.1e6
    assert not conn.in_transaction                                         # committed


def test_set_direction_back_and_forth_restores_the_marks_and_the_same_direction_touches_nothing():
    conn = schema.connect()
    _seed_swap(conn)
    irs_direction.set_direction(conn, "918421481", "pay ")                 # case / spaces forgiven; already PAY
    assert _swap_state(conn) == (625e6, {"FIXED": -625e6, "FLOAT": 625e6})
    _seed_marks(conn)
    seeded = _marks_of(conn)
    irs_direction.set_direction(conn, "918421481", "PAY")                  # unchanged: marks were priced this way
    assert _marks_of(conn) == seeded
    irs_direction.set_direction(conn, "918421481", "RECEIVE")
    pricer_rows = [m for m in seeded if m[2] == "QL_PRICER"]               # the BBG_BDH PV rows are gone for good
    assert _marks_of(conn) == sorted((d, mt, s, v if mt == "PAR_RATE" else -v) for d, mt, s, v in pricer_rows)
    irs_direction.set_direction(conn, "918421481", "PAY")
    assert _marks_of(conn) == pricer_rows                                  # -(-x) = x: history intact
    assert _swap_state(conn) == (625e6, {"FIXED": -625e6, "FLOAT": 625e6})
    assert irs_direction.get_overrides(conn) == {"918421481": "PAY"}       # upsert, one row
    assert conn.execute("SELECT COUNT(*) FROM irs_direction_overrides").fetchone()[0] == 1


def test_choosing_the_direction_a_swap_already_has_still_records_the_override_and_touches_nothing_else():
    """How the user says "yes, this one really is pay fixed": the Rates table's control
    sends PAY for a swap that is already PAY. That must store the override (clearing the
    needs-your-choice flag) while the trade, its legs and every mark stay as they are."""
    conn = schema.connect()
    _seed_swap(conn)
    _seed_marks(conn)
    conn.execute("INSERT INTO realised_pnl VALUES ('918421481',?,'IRS','USD','2027-02-11',5.0,0.0,'PV_USD',1.0,"
                 "'2027-02-11','QL_PRICER',5.0,'t','')", (SWAP,))
    conn.commit()
    state, marks = _swap_state(conn), _marks_of(conn)
    assert irs_direction.needs_user_choice(conn) == ["918421481"]

    irs_direction.set_direction(conn, "918421481", "PAY")

    assert irs_direction.get_overrides(conn) == {"918421481": "PAY"}
    assert irs_direction.needs_user_choice(conn) == []
    (row,) = irs_direction.direction_report(conn)
    assert (row["direction"], row["source"], row["needs_user_choice"]) == ("PAY", "USER", False) and row["set_at"]
    assert _swap_state(conn) == state and _marks_of(conn) == marks
    assert conn.execute("SELECT COUNT(*) FROM realised_pnl").fetchone()[0] == 1
    # and the same for a receiver confirmed as a receiver
    irs_direction.set_direction(conn, "918421481", "RECEIVE")
    turned = _marks_of(conn)
    irs_direction.set_direction(conn, "918421481", "RECEIVE")
    assert _marks_of(conn) == turned


def test_direction_report_keys_are_the_ones_the_rates_table_was_built_against():
    conn = schema.connect()
    _seed_swap(conn)
    (row,) = irs_direction.direction_report(conn)
    assert set(row) == {"trade_id", "instrument_id", "direction", "source", "needs_user_choice", "note", "set_at"}


def test_a_flip_deletes_instead_of_reversing_what_it_cannot_attribute_or_cannot_negate():
    conn = schema.connect()
    _seed_swap(conn)
    _seed_swap(conn, "920118423", SWAP, 995e6)                             # two trades on ONE instrument
    _seed_marks(conn)
    irs_direction.set_direction(conn, "918421481", "RECEIVE")
    assert {m[1] for m in _marks_of(conn)} == {"PAR_RATE"}                 # whose marks were they? deleted
    # a stored value that is not a number cannot be negated: that row is deleted, the rest reversed
    conn = schema.connect()
    _seed_swap(conn)
    _seed_marks(conn)
    conn.execute("UPDATE marks SET value = '24-Jul' WHERE as_of_date = '2026-09-16' AND mark_type = 'PV_USD' AND source = 'QL_PRICER'")
    conn.commit()
    irs_direction.set_direction(conn, "918421481", "RECEIVE")
    assert ("2026-09-16", "PV_USD") not in {(m[0], m[1]) for m in _marks_of(conn)}
    assert ("2026-09-17", "PV_USD", "QL_PRICER", -1.0) in _marks_of(conn)


# --------------------------------------------------------------------------- the identity
try:
    import QuantLib  # noqa: F401
    HAVE_QUANTLIB = True
except ImportError:
    HAVE_QUANTLIB = False

MOCK_USD_SOFR = [("1W", 0.0530), ("1M", 0.0528), ("3M", 0.0520), ("6M", 0.0505), ("1Y", 0.0480),
                 ("2Y", 0.0440), ("5Y", 0.0410), ("10Y", 0.0415), ("30Y", 0.0400)]
IDENTITY_AS_OF = "2026-09-17"


@pytest.fixture
def clean_quantlib_fixings():
    """QuantLib keeps index fixings in a process-wide store, so the SOFR fixings these
    tests load would still be there for whichever test runs next -- including
    tests/test_rates_pricing.py's check that a seasoned swap FAILS without fixings.
    Cleared before and after, so these tests neither depend on nor leak that state."""
    import QuantLib as ql

    ql.IndexManager.instance().clearHistories()
    yield
    ql.IndexManager.instance().clearHistories()


def _priced_swap_db(quantity, effective, maturity, as_ofs=(IDENTITY_AS_OF,)):
    """A database with one USD SOFR OIS swap, a curve and fixings for each as-of date,
    priced by engine/rates itself (used read-only: nothing in engine/ is changed)."""
    import datetime

    from engine.rates.store import price_and_store

    conn = schema.connect()
    _seed_swap(conn, quantity=quantity)
    conn.execute("UPDATE trade_legs SET start_date = ?, settle_date = ?", (effective, maturity))
    conn.execute("UPDATE instruments SET expiry_date = ?", (maturity,))
    for as_of in as_ofs:
        conn.executemany('INSERT INTO curve_quotes (as_of_date, ccy, "index", tenor, ticker, value, quote_type, field, source) '
                         "VALUES (?,'USD','SOFR',?,?,?,'OIS','PX_LAST','BBG_BDP')",
                         [(as_of, t, "USD" + t, v) for t, v in MOCK_USD_SOFR])
    day, fixings = datetime.date(2025, 8, 1), []
    while day <= datetime.date.fromisoformat(max(as_ofs)):
        if day.weekday() < 5:
            fixings.append(("SOFR", day.isoformat(), 0.0530, "BBG_BDH"))
        day += datetime.timedelta(days=1)
    conn.executemany('INSERT INTO index_fixings ("index", fixing_date, value, source) VALUES (?,?,?,?)', fixings)
    conn.commit()
    for as_of in as_ofs:
        price_and_store(conn, as_of, "918421481")
    return conn


def _pricer_marks(conn):
    return {(d, mt): v for d, mt, v in conn.execute(
        "SELECT as_of_date, mark_type, value FROM marks WHERE source = 'QL_PRICER'")}


@pytest.mark.skipif(not HAVE_QUANTLIB, reason="QuantLib not installed")
@pytest.mark.parametrize("label,effective,maturity", [
    ("forward-starting", "2026-11-11", "2028-11-11"),
    ("seasoned, a coupon already paid", "2025-08-12", "2028-08-12"),
    ("expired", "2025-08-12", "2026-02-12"),
])
def test_receiver_marks_are_exactly_minus_the_payers_so_reversing_history_is_exact(
        clean_quantlib_fixings, label, effective, maturity):
    """THE PROOF behind keeping a flipped swap's history by sign reversal: price the same
    swap on the same curve as a payer and as a receiver with engine/rates. PV_USD,
    DV01_USD (stored signed) and CASHFLOW_USD must be exact negatives (1e-6 relative)
    and PAR_RATE equal, in every regime. Then flip the payer with set_direction and
    check the reversed marks ARE what the pricer writes for the receiver. The reversal
    itself is the pricer's, engine/rates/store.py::reverse_direction_marks (moved there
    2026-09-22, hard rule 2: the pricer reverses its own marks), which set_direction
    calls; tests/test_rates_pricing.py proves the function on its own."""
    payer = _priced_swap_db(625e6, effective, maturity)
    receiver = _priced_swap_db(-625e6, effective, maturity)
    p, r = _pricer_marks(payer), _pricer_marks(receiver)
    assert set(p) == set(r) and {mt for _, mt in p} >= {"PV_USD", "DV01_USD", "CASHFLOW_USD"}
    for key, value in p.items():
        if key[1] == "PAR_RATE":
            assert r[key] == pytest.approx(value, rel=1e-12), label
        else:
            assert r[key] == pytest.approx(-value, rel=1e-6, abs=1e-9), (label, key)
    if label != "expired":
        assert p[(IDENTITY_AS_OF, "PV_USD")] != 0 and p[(IDENTITY_AS_OF, "DV01_USD")] != 0   # not trivially true
    if label != "forward-starting":
        assert p[(IDENTITY_AS_OF, "CASHFLOW_USD")] != 0
    irs_direction.set_direction(payer, "918421481", "RECEIVE")
    flipped = _pricer_marks(payer)
    assert set(flipped) == set(r)
    for key, value in r.items():
        assert flipped[key] == pytest.approx(value, rel=1e-6, abs=1e-9), (label, key)


@pytest.mark.skipif(not HAVE_QUANTLIB, reason="QuantLib not installed")
def test_a_flipped_swaps_history_survives_on_every_date_and_matches_a_reprice(clean_quantlib_fixings):
    """Three as-of dates of history: after the flip each date holds what pricing the
    receiver on that date's curve gives, so Daily / 5d / MTD / YTD keep working."""
    dates = ("2026-09-15", "2026-09-16", "2026-09-17")
    payer = _priced_swap_db(625e6, "2025-08-12", "2028-08-12", dates)
    receiver = _priced_swap_db(-625e6, "2025-08-12", "2028-08-12", dates)
    irs_direction.set_direction(payer, "918421481", "RECEIVE")
    flipped, repriced = _pricer_marks(payer), _pricer_marks(receiver)
    assert {d for d, _ in flipped} == set(dates) and set(flipped) == set(repriced)
    for key, value in repriced.items():
        assert flipped[key] == pytest.approx(value, rel=1e-6, abs=1e-9), key


def test_a_flip_hands_the_mark_reversal_to_the_pricer_and_keeps_no_copy_of_its_own(monkeypatch):
    """irs_direction writes trades and legs; the marks are engine/rates' own and are
    reversed by engine.rates.store.reverse_direction_marks, called once per real flip
    with (conn, trade_id, instrument_id) inside the same transaction. A choice that
    does not flip the swap never calls it. Imported lazily: this module loads no
    QuantLib at import time."""
    import engine.rates.store as store

    assert not hasattr(irs_direction, "_reverse_priced")
    calls = []
    monkeypatch.setattr(store, "reverse_direction_marks",
                        lambda conn, trade_id, instrument_id: calls.append((conn.in_transaction, trade_id, instrument_id)))
    conn = schema.connect()
    _seed_swap(conn)
    irs_direction.set_direction(conn, "918421481", "PAY")          # already pay fixed: no flip, no call
    assert calls == []
    irs_direction.set_direction(conn, "918421481", "RECEIVE")      # a real flip: the pricer is asked once
    assert calls == [(True, "918421481", SWAP)]
    before = irs_direction.irs_signs(conn)
    conn.execute("UPDATE trades SET quantity = -quantity")
    conn.commit()
    assert irs_direction.reverse_flipped(conn, before) == 1
    assert calls == [(True, "918421481", SWAP)] * 2


def test_set_direction_raises_for_unknown_trade_non_irs_trade_and_bad_direction():
    conn = schema.connect()
    _seed_swap(conn)
    conn.execute("INSERT INTO instruments VALUES ('USDJPY','FX','USD','JPY',1,0,'USDJPY Curncy','9999-12-31')")
    conn.execute("INSERT INTO trades VALUES ('fx1','XLSX','USDJPY','FX_FWD','fx1','2026-08-20',1e6,158.0,'a','c','','t','d','')")
    conn.commit()
    with pytest.raises(ValueError, match="no trade"):
        irs_direction.set_direction(conn, "nope", "PAY")
    with pytest.raises(ValueError, match="not an interest rate swap"):
        irs_direction.set_direction(conn, "fx1", "RECEIVE")
    for bad in ("BUY", "", None, "payer"):
        with pytest.raises(ValueError, match="PAY or RECEIVE"):
            irs_direction.set_direction(conn, "918421481", bad)
    assert irs_direction.get_overrides(conn) == {}                         # nothing was stored
    assert _swap_state(conn)[0] == 625e6


def test_set_direction_is_all_or_nothing_even_on_an_autocommit_connection(tmp_path):
    path = tmp_path / "auto.db"
    schema.connect(path).close()
    conn = sqlite3.connect(str(path), isolation_level=None)                # `with conn:` protects nothing here
    _seed_swap(conn)
    conn.execute("DROP TABLE marks")
    conn.execute("CREATE TABLE marks (instrument_id TEXT)")                # the purge will fail: no mark_type column
    with pytest.raises(sqlite3.OperationalError):
        irs_direction.set_direction(conn, "918421481", "RECEIVE")
    assert _swap_state(conn) == (625e6, {"FIXED": -625e6, "FLOAT": 625e6})   # rolled back, not half-flipped
    assert irs_direction.get_overrides(conn) == {}


def test_irs_direction_works_on_a_database_that_predates_the_table(tmp_path):
    path = tmp_path / "old.db"
    conn = schema.connect(path)
    _seed_swap(conn)
    conn.execute("DROP TABLE irs_direction_overrides")                     # as an older database would be
    conn.commit()
    assert irs_direction.get_overrides(conn) == {}                         # readers create nothing
    assert irs_direction.apply_overrides(conn) == 0
    assert [r["source"] for r in irs_direction.direction_report(conn)] == ["DEFAULT"]
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='irs_direction_overrides'").fetchone()[0] == 0
    irs_direction.set_direction(conn, "918421481", "RECEIVE")              # the writer creates it, no migration step
    assert irs_direction.get_overrides(conn) == {"918421481": "RECEIVE"}
    conn.close()
    ro = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)             # the UI's read-only handle
    assert irs_direction.get_overrides(ro) == {"918421481": "RECEIVE"}
    assert irs_direction.direction_report(ro)[0]["source"] == "USER"
    ro.close()


def test_apply_overrides_re_applies_to_the_trades_on_file_and_returns_how_many():
    conn = schema.connect()
    _seed_swap(conn)
    _seed_swap(conn, "920118423", "IRSOIS-USD-22870318", 995e6)
    irs_direction.set_direction(conn, "918421481", "RECEIVE")
    irs_direction.set_direction(conn, "920118423", "PAY")
    conn.execute("INSERT INTO irs_direction_overrides VALUES ('gone-from-book','RECEIVE','2026-09-18')")
    # a re-upload rewrites the book from the file, which says pay fixed for everything
    conn.execute("UPDATE trades SET quantity = ABS(quantity)")
    conn.execute("UPDATE trade_legs SET amount = CASE leg_type WHEN 'FIXED' THEN -ABS(amount) ELSE ABS(amount) END")
    conn.commit()
    assert irs_direction.apply_overrides(conn) == 2                        # the two on file; not the absent one
    assert _swap_state(conn) == (-625e6, {"FIXED": 625e6, "FLOAT": -625e6})
    assert _swap_state(conn, "920118423") == (995e6, {"FIXED": -995e6, "FLOAT": 995e6})
    assert irs_direction.apply_overrides(conn) == 2                        # idempotent
    assert irs_direction.get_overrides(conn)["gone-from-book"] == "RECEIVE"   # kept for when it comes back


def test_direction_report_flags_the_swaps_still_waiting_for_the_users_choice():
    from data.ingest.common import NO_DIRECTION_SIGNAL

    conn = schema.connect()
    _seed_swap(conn)                                                       # PAY, nothing decided it
    _seed_swap(conn, "920118423", "IRSOIS-USD-22870318", -995e6)           # RECEIVE from a short marker in the file
    _seed_swap(conn, "932385416", "IRSOIS-USD-22919058", 158.22e6)
    irs_direction.set_direction(conn, "932385416", "RECEIVE")              # the user's choice
    report = {r["trade_id"]: r for r in irs_direction.direction_report(conn)}
    assert (report["918421481"]["direction"], report["918421481"]["source"], report["918421481"]["note"]) == \
           ("PAY", "DEFAULT", NO_DIRECTION_SIGNAL)
    assert (report["920118423"]["direction"], report["920118423"]["source"]) == ("RECEIVE", "FILE")
    assert (report["932385416"]["direction"], report["932385416"]["source"]) == ("RECEIVE", "USER")
    assert report["932385416"]["set_at"] and not report["918421481"]["set_at"]
    assert irs_direction.needs_user_choice(conn) == ["918421481"]
    irs_direction.set_direction(conn, "918421481", "PAY")                  # confirming PAY clears the flag
    assert irs_direction.needs_user_choice(conn) == []
    assert irs_direction.clear_direction(conn, "918421481") is True
    assert irs_direction.clear_direction(conn, "918421481") is False
    assert irs_direction.needs_user_choice(conn) == ["918421481"]


def test_reverse_flipped_turns_round_only_the_swaps_that_flipped_since_the_snapshot():
    conn = schema.connect()
    _seed_swap(conn)
    _seed_swap(conn, "920118423", "IRSOIS-USD-22870318", 995e6)
    _seed_marks(conn)
    _seed_marks(conn, "IRSOIS-USD-22870318")
    untouched = _marks_of(conn, "IRSOIS-USD-22870318")
    before = irs_direction.irs_signs(conn)
    assert before == {"918421481": 1, "920118423": 1}
    conn.execute("UPDATE trades SET quantity = -quantity WHERE trade_id = '918421481'")
    _seed_swap(conn, "932385416", "IRSOIS-USD-22919058", -158.22e6)        # new since the snapshot: not a flip
    _seed_marks(conn, "IRSOIS-USD-22919058")
    conn.commit()
    assert irs_direction.reverse_flipped(conn, before) == 1
    assert {(mt, s): v for _, mt, s, v in _marks_of(conn)} == {
        ("PV_USD", "QL_PRICER"): -1.0, ("DV01_USD", "QL_PRICER"): -1.0, ("CASHFLOW_USD", "QL_PRICER"): -1.0,
        ("PAR_RATE", "QL_PRICER"): 1.0}                                    # BBG_BDH PV rows deleted
    assert _marks_of(conn, "IRSOIS-USD-22870318") == untouched
    assert all(v == 1.0 for *_, v in _marks_of(conn, "IRSOIS-USD-22919058"))
    assert irs_direction.purge_flipped is irs_direction.reverse_flipped    # the old name still works


def test_apply_overrides_turns_marks_round_but_reapply_after_load_leaves_them_to_reverse_flipped():
    def flipped_back_by_a_reload():
        conn = schema.connect()
        _seed_swap(conn)
        irs_direction.set_direction(conn, "918421481", "RECEIVE")
        _seed_marks(conn)                                                  # priced as a receiver
        conn.execute("UPDATE trades SET quantity = ABS(quantity)")         # what a file rewrite does
        conn.commit()
        return conn

    conn = flipped_back_by_a_reload()
    seeded = _marks_of(conn)
    assert irs_direction.reapply_after_load(conn) == 1
    assert _swap_state(conn)[0] == -625e6 and _marks_of(conn) == seeded    # receiver again, marks never moved
    conn = flipped_back_by_a_reload()
    assert irs_direction.apply_overrides(conn) == 1                        # standalone: a real flip on file
    assert ("2026-09-17", "PV_USD", "QL_PRICER", -1.0) in _marks_of(conn)
