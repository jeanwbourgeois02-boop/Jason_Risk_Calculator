"""SQLite schema for the risk monitor, transcribed from CLAUDE.md "Data contract -> Tables".

`create_schema(conn)` is idempotent (CREATE ... IF NOT EXISTS) and enables foreign keys.
`connect(path)` opens a connection and applies the schema.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Union

TABLES = ("instruments", "trades", "trade_legs", "marks", "curves", "positions")
VIEWS = ("marks_official",)

# Official source per mark_type (CLAUDE.md "Official marks"). BNP_BVAL is never official.
OFFICIAL_MARK_SOURCE = {
    "SPOT": "BBG_BFXFORWARD",
    "FWD_OUTRIGHT": "BBG_BFXFORWARD",
    "FUTURE_PX": "BBG_BDH",
    "PAR_RATE": "BBG_BDH",
    "PV_USD": "BBG_BDH",
    "DV01_USD": "BBG_BDH",
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
  description     TEXT NOT NULL
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


def _marks_official_ddl() -> str:
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
"""


# --------------------------------------------------------------------------- P&L ledger
# Additive tables for engine/pnl/ledger.py (realised P&L on settlement + daily snapshot
# series for Daily / 5d / MTD / YTD). They never feed the exposure or workbook formulas.
LEDGER_TABLES = ("realised_pnl", "pnl_snapshots")
_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS realised_pnl (
  trade_id            TEXT PRIMARY KEY REFERENCES trades,
  instrument_id       TEXT NOT NULL,
  currency            TEXT NOT NULL,
  settle_date         TEXT NOT NULL,
  local_amount        REAL NOT NULL,
  usd_entry_amount    REAL NOT NULL,
  spot_usd_per_local  REAL NOT NULL,
  spot_as_of_date     TEXT NOT NULL,      -- date of the SPOT mark used to freeze (settle date, or last prior)
  spot_source         TEXT NOT NULL,
  pnl_usd             REAL NOT NULL,
  frozen_at           TEXT NOT NULL,
  note                TEXT NOT NULL       -- '' or 'spot dated <d> (last before settlement)'
);
CREATE TABLE IF NOT EXISTS pnl_snapshots (
  as_of_date          TEXT PRIMARY KEY,
  snapped_at          TEXT NOT NULL,
  realised_ltd_usd    REAL NOT NULL,
  unrealised_usd      REAL NOT NULL,      -- NaN stored as NULL is not allowed: incomplete -> complete = 0 and 0.0
  total_ltd_usd       REAL NOT NULL,
  net_usd             REAL NOT NULL,
  gross_usd           REAL NOT NULL,
  trading_usd         REAL NOT NULL,
  open_trades         INTEGER NOT NULL,
  realised_trades     INTEGER NOT NULL,
  complete            INTEGER NOT NULL,   -- 1 = every open currency priced and every settled trade realised
  missing             TEXT NOT NULL       -- comma list of unpriced currencies / unrealisable trade ids
);
"""


def create_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and the marks_official view if absent; enable foreign keys."""
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_DDL + _marks_official_ddl() + _LEDGER_DDL)
    conn.commit()


def connect(path: Union[str, Path] = ":memory:") -> sqlite3.Connection:
    """Open (or create) a database at `path` and apply the schema."""
    conn = sqlite3.connect(str(path))
    create_schema(conn)
    return conn
