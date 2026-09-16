"""SQLite schema for the risk monitor, transcribed from CLAUDE.md "Data contract -> Tables".

`create_schema(conn)` is idempotent (CREATE ... IF NOT EXISTS) and enables foreign keys.
`connect(path)` opens a connection and applies the schema.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Union

TABLES = ("instruments", "trades", "trade_legs", "marks", "curves", "positions", "instrument_theme",
          "curve_quotes")
VIEWS = ("marks_official", "trades_official")

# Official source per mark_type (CLAUDE.md "Official marks"). BNP_BVAL is never official.
# PAR_RATE / PV_USD / DV01_USD: changed from BBG_BDH to QL_PRICER per housekeeper
# authorization 2026-09-15 (rates-pricer's own bootstrap becomes official for these three
# mark_types; BBG_BDH becomes reconciliation-only for IRS, mirroring how BNP_BVAL is
# reconciliation-only for FX). No other mark_type mapping changed.
OFFICIAL_MARK_SOURCE = {
    "SPOT": "BBG_BFXFORWARD",
    "FWD_OUTRIGHT": "BBG_BFXFORWARD",
    "FUTURE_PX": "BBG_BDH",
    "PAR_RATE": "QL_PRICER",
    "PV_USD": "QL_PRICER",
    "DV01_USD": "QL_PRICER",
    "DELTA": "MANUAL",
    "PREMIUM": "MANUAL",
}

_DDL = """
CREATE TABLE IF NOT EXISTS instruments (
  instrument_id   TEXT PRIMARY KEY,
  asset_class     TEXT NOT NULL,
  base_ccy        TEXT NOT NULL,
  quote_ccy       TEXT NOT NULL,
  multiplier      REAL NOT NULL,
  is_ndf          INTEGER NOT NULL,
  bbg_ticker      TEXT NOT NULL,
  expiry_date     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
  trade_id        TEXT PRIMARY KEY,
  source          TEXT NOT NULL,
  instrument_id   TEXT NOT NULL REFERENCES instruments,
  product         TEXT NOT NULL,
  package_id      TEXT NOT NULL,
  trade_date      TEXT NOT NULL,
  quantity        REAL NOT NULL,
  price           REAL NOT NULL,
  account         TEXT NOT NULL,
  counterparty    TEXT NOT NULL,
  strategy        TEXT NOT NULL,
  trader          TEXT NOT NULL,
  description     TEXT NOT NULL,
  theme           TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS instrument_theme (
  instrument_id   TEXT PRIMARY KEY REFERENCES instruments,
  theme           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_legs (
  trade_id        TEXT NOT NULL REFERENCES trades,
  leg_no          INTEGER NOT NULL,
  leg_type        TEXT NOT NULL,
  ccy             TEXT NOT NULL,
  amount          REAL NOT NULL,
  start_date      TEXT NOT NULL,
  settle_date     TEXT NOT NULL,
  rate            REAL NOT NULL,
  settles_cash    INTEGER NOT NULL,
  PRIMARY KEY (trade_id, leg_no)
);

CREATE TABLE IF NOT EXISTS marks (
  as_of_date      TEXT NOT NULL,
  instrument_id   TEXT NOT NULL REFERENCES instruments,
  settle_date     TEXT NOT NULL,
  mark_type       TEXT NOT NULL,
  value           REAL NOT NULL,
  source          TEXT NOT NULL,
  snapped_at      TEXT NOT NULL,
  PRIMARY KEY (as_of_date, instrument_id, settle_date, mark_type, source)
);

CREATE TABLE IF NOT EXISTS curves (
  curve_id        TEXT NOT NULL,
  as_of_date      TEXT NOT NULL,
  node_date       TEXT NOT NULL,
  discount_factor REAL NOT NULL,
  par_rate        REAL NOT NULL,
  source          TEXT NOT NULL,
  PRIMARY KEY (curve_id, as_of_date, node_date, source)
);

-- Raw curve-quote staging table, written by data/bloomberg (bbg-data), consumed by the
-- IRS pricer bootstrap (rates-pricer) to build `curves`. This is NOT `curves` itself:
-- `curves` holds bootstrapped discount factors / par rates per node; this table holds the
-- unprocessed Bloomberg quotes (deposit / swap ticks) the bootstrap reads to build them.
CREATE TABLE IF NOT EXISTS curve_quotes (
  as_of_date      TEXT NOT NULL,
  ccy             TEXT NOT NULL,
  "index"         TEXT NOT NULL,
  tenor           TEXT NOT NULL,
  ticker          TEXT NOT NULL,
  value           REAL NOT NULL,
  quote_type      TEXT NOT NULL,
  field           TEXT NOT NULL,
  source          TEXT NOT NULL,
  PRIMARY KEY (as_of_date, ccy, "index", tenor, source)
);

CREATE TABLE IF NOT EXISTS positions (
  as_of_date      TEXT NOT NULL,
  source          TEXT NOT NULL,
  account         TEXT NOT NULL,
  instrument_id   TEXT NOT NULL REFERENCES instruments,
  settle_date     TEXT NOT NULL,
  quantity        REAL NOT NULL,
  cost_local      REAL NOT NULL,
  mark            REAL NOT NULL,
  fx_to_usd       REAL NOT NULL,
  mv_local        REAL NOT NULL,
  mv_usd          REAL NOT NULL,
  pnl_dtd_usd     REAL NOT NULL,
  pnl_mtd_usd     REAL NOT NULL,
  pnl_ytd_usd     REAL NOT NULL,
  PRIMARY KEY (as_of_date, source, account, instrument_id, settle_date)
);
"""


def _views_ddl() -> str:
    cases = "\n".join(
        f"      WHEN '{mt}' THEN '{src}'" for mt, src in OFFICIAL_MARK_SOURCE.items()
    )
    return f"""
CREATE VIEW IF NOT EXISTS marks_official AS
SELECT as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at
FROM marks
WHERE source = CASE mark_type
{cases}
    END;

-- trades_official (user decision 2026-09-16): the app now has two trade sources for the
-- same book -- the real-time blotter ('XLSX') and the once-daily BNP EOD-Hong-Kong
-- snapshot ('BNP'). BNP is kept for its `positions` cash-balance snapshot and for
-- BNP_BVAL reconciliation marks, but is NOT authoritative for trade-level exposure/P&L
-- any more: the blotter is the primary trade source and is expected to carry every live
-- trade BNP also carries, under a different trade_id scheme (docs/open-questions.md item
-- 55 -- not yet deduplicated). Without this view, a trade loaded from both sources would
-- be summed twice into the ladder/delta/P&L. Every engine query that computes real
-- exposure or P&L must read `trades_official`, never `trades` directly, with the sole
-- exception of engine/pnl/reconcile.py, which explicitly needs both sources to compare
-- them against each other.
CREATE VIEW IF NOT EXISTS trades_official AS
SELECT * FROM trades WHERE source != 'BNP';
"""


# --------------------------------------------------------------------------- P&L ledger
# Additive table for engine/pnl/ledger.py (realised P&L on settlement, consumed for
# Daily / 5d / MTD / YTD via ltd() recomputation). Never feeds the exposure or workbook
# formulas. `pnl_snapshots` is retired per docs/BUILD_PLAN.md section 3 ("pnl_snapshots
# is retired") -- its DDL is intentionally no longer created here. Existing databases
# that still have the table are left alone (not dropped) since engine/pnl/ledger.py and
# data/bloomberg/backfill.py are migrated separately by their own owning agents.
LEDGER_TABLES = ("realised_pnl",)
_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS realised_pnl (
  trade_id            TEXT PRIMARY KEY REFERENCES trades,
  instrument_id       TEXT NOT NULL,
  product             TEXT NOT NULL DEFAULT 'FX_FWD',   -- FX_FWD | FX_SWAP | FUTURE | FX_OPTION | IRS
  currency            TEXT NOT NULL,
  settle_date         TEXT NOT NULL,
  local_amount        REAL NOT NULL,
  usd_entry_amount    REAL NOT NULL,      -- nullable-by-sentinel 0.0: crosses/futures have no single USD
                                          -- entry leg, so 0.0 here means "not applicable", not "zero P&L"
  mark_type           TEXT NOT NULL DEFAULT 'SPOT',      -- SPOT | FUTURE_PX: which mark_type froze this row
  spot_usd_per_local  REAL NOT NULL,
  spot_as_of_date     TEXT NOT NULL,      -- date of the mark used to freeze (settle date, or last prior)
  spot_source         TEXT NOT NULL,
  pnl_usd             REAL NOT NULL,
  frozen_at           TEXT NOT NULL,
  note                TEXT NOT NULL       -- '' or 'spot dated <d> (last before settlement)'
);
"""

# Ambiguous FX-swap package candidates (CLAUDE.md "package_id rule"): groups with more
# than one candidate on a side are never auto-grouped and land here for manual review
# instead. Populated by data/ingest/swaps.py::package_swaps.
_SWAP_REVIEW_DDL = """
CREATE TABLE IF NOT EXISTS swap_review (
  candidate_group     TEXT NOT NULL,      -- 'account|instrument_id|trade_date'
  trade_id            TEXT NOT NULL REFERENCES trades,
  reason              TEXT NOT NULL,
  PRIMARY KEY (candidate_group, trade_id)
);
"""

# --------------------------------------------------------------------------- bundles
# A bundle (Blotter "Bundles" sub-tab, user decision 2026-09-15) is a named view whose
# membership is the existing `instrument_theme` mechanism (theme = bundle name); this
# table only holds the bundle's own metadata, never membership (that stays in
# `instrument_theme` / `trades.theme` so `period_pnl_by(..., 'theme')` already groups
# a bundle's trades with no join needed).
BUNDLE_TABLES = ("bundles",)
_BUNDLES_DDL = """
CREATE TABLE IF NOT EXISTS bundles (
  name            TEXT PRIMARY KEY,
  description     TEXT NOT NULL,
  created_at      TEXT NOT NULL
);
"""


def _migrate_columns(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a table's initial CREATE, for databases created by
    an older version of this module. CREATE TABLE IF NOT EXISTS above only helps brand
    new databases; existing ones need an explicit ALTER TABLE ADD COLUMN."""
    additions = [
        ("trades", "theme", "TEXT NOT NULL DEFAULT ''"),
        ("realised_pnl", "product", "TEXT NOT NULL DEFAULT 'FX_FWD'"),
        ("realised_pnl", "mark_type", "TEXT NOT NULL DEFAULT 'SPOT'"),
    ]
    for table, column, coldef in additions:
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue  # table itself doesn't exist yet; the CREATE above will make it right
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coldef}")


def create_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and the marks_official view if absent; enable foreign keys."""
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_DDL + _views_ddl() + _LEDGER_DDL + _SWAP_REVIEW_DDL + _BUNDLES_DDL)
    _migrate_columns(conn)
    conn.commit()


def connect(path: Union[str, Path] = ":memory:") -> sqlite3.Connection:
    """Open (or create) a database at `path` and apply the schema."""
    conn = sqlite3.connect(str(path))
    create_schema(conn)
    return conn
