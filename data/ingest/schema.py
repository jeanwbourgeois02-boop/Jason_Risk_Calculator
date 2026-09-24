"""SQLite schema for the risk monitor, transcribed from CLAUDE.md "Data contract -> Tables".

`create_schema(conn)` is idempotent (CREATE ... IF NOT EXISTS) and enables foreign keys.
`connect(path)` opens a connection and applies the schema.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Tuple, Union

TABLES = ("instruments", "trades", "trade_legs", "marks", "curves", "instrument_theme",
          "curve_quotes", "instrument_options")
VIEWS = ("marks_official", "trades_official")

# Official source per mark_type (CLAUDE.md "Official marks"). BNP_BVAL is never official.
# 2026-09-24 (commodity conversion Phase 2, user decision recorded in CLAUDE.md "Commodity
# conversion plan": rates, NDFs, the FX-swap package rule and the equity index leave the
# app): PAR_RATE / PV_USD / DV01_USD / CASHFLOW_USD (the swap pricer's marks) and NDF_1M /
# NDF_FIX (the NDF rate and fixing) are no longer mark types. Nothing writes or reads them;
# the view is recreated on every startup, so an old database's rows of those types simply
# stop being official (a mark_type with no branch here matches no source).
OFFICIAL_MARK_SOURCE = {
    "SPOT": "BBG_BFXFORWARD",
    "FWD_OUTRIGHT": "BBG_BFXFORWARD",
    "FUTURE_PX": "BBG_BDH",
    # DELTA / PREMIUM: changed from MANUAL to QL_OPTIONS_PRICER 2026-09-17 (options_calc
    # merge Phase 2) -- engine/options writes these from the vendored QuantLib pricers;
    # hand-typed MANUAL marks are reconciliation-only.
    "DELTA": "QL_OPTIONS_PRICER",
    "PREMIUM": "QL_OPTIONS_PRICER",
    "GAMMA": "QL_OPTIONS_PRICER",
    "THETA": "QL_OPTIONS_PRICER",
    "VEGA": "QL_OPTIONS_PRICER",
    "RHO": "QL_OPTIONS_PRICER",
    # DELTA_PA (2026-09-17, options merge Phase 7): premium-adjusted delta for pairs
    # whose G10 convention is premium-adjusted; DELTA itself is unchanged and stays
    # what the ladder reads. Same pricer, same official source as the other Greeks.
    "DELTA_PA": "QL_OPTIONS_PRICER",
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

-- instruments.is_ndf is kept for old databases and old INSERTs (NDFs left the app
-- 2026-09-24); nothing writes 1 there any more.
--
-- Option-specific attributes, kept out of `instruments` itself so every non-option row
-- (FX/FUTURE -- the overwhelming majority) doesn't carry sentinel columns it never
-- uses, and so the ~70 existing test fixtures that INSERT INTO instruments with the
-- original 8-column shape keep working unchanged (housekeeper decision, 2026-09-17,
-- options_calc merge Phase 1).
CREATE TABLE IF NOT EXISTS instrument_options (
  instrument_id   TEXT PRIMARY KEY REFERENCES instruments,
  strike          REAL NOT NULL DEFAULT 0,          -- 0 = not known (never fabricated)
  option_type     TEXT NOT NULL DEFAULT '',         -- CALL | PUT | ''
  barrier_level   REAL NOT NULL DEFAULT 0,          -- barrier / touch level; 0 = n/a
  avg_start_date  TEXT NOT NULL DEFAULT '9999-12-31', -- Asian averaging start; sentinel = n/a
  payoff          TEXT NOT NULL DEFAULT 'VANILLA'   -- VANILLA | DIGITAL | BARRIER_KI | BARRIER_KO
                                                    -- | ASIAN | ONE_TOUCH | NO_TOUCH | AMERICAN
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

-- Raw curve-quote staging table, written by data/bloomberg (bbg-curves), consumed by the
-- OIS curve bootstrap (rates-pricer) to build `curves`, the discount curves the option
-- pricers read. This is NOT `curves` itself: `curves` holds bootstrapped discount factors /
-- par rates per node; this table holds the unprocessed Bloomberg OIS quotes.
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

"""
# Retired 2026-09-24 with the swaps (commodity conversion Phase 2): `index_fixings` (a
# seasoned OIS swap's past fixings), `irs_direction_overrides` (the user's pay / receive
# per swap) and `swap_review` (the FX-swap package rule's ambiguous candidates) are no
# longer created on a new database. An old database keeps `index_fixings` and
# `irs_direction_overrides` untouched (nothing reads or writes them; harmless);
# `purge_retired_sources` drops `swap_review`, whose foreign key to `trades` could
# otherwise block deleting a trade it names.
# `positions` (one row per PB position per day, BNP grain) was dropped from the schema
# 2026-09-17 ("no bnp fall back", docs/bnp-excel-removal.md): its only writer was the
# retired BNP CSV parser, and its only readers (engine/pnl/reconcile.py, the ladder's
# CASH column) were removed the same day. `purge_retired_sources` below drops the table
# outright on any existing database that still has it from before this change.


# Official FALLBACK per mark_type (user decision 2026-09-18): a row from this source is
# official only where no row from OFFICIAL_MARK_SOURCE exists for the same
# (as_of_date, instrument_id, settle_date, mark_type). Only FWD_OUTRIGHT has one:
# Bloomberg's API serves standard tenors, so a leg or option expiry on a broken date can
# only ever be BBG_INTERP (linear interpolation between the two bracketing tenor outrights
# of Bloomberg's own FWD_CURVE, never extrapolated) -- exactly what the Excel BFXForward
# call did for broken dates -- and without it 720 of 743 forwards in the reference book
# had no P&L while the pull reported every request OK (2026-09-18 audit). SPOT and
# FUTURE_PX never fall back: BBG_INTERP stays reconciliation-only for every other
# mark_type. The view keeps (as_of_date, instrument_id, settle_date, mark_type) unique.
OFFICIAL_FALLBACK_SOURCE = {
    "FWD_OUTRIGHT": "BBG_INTERP",
}


def _views_ddl() -> str:
    """Every view is dropped and recreated unconditionally on every `create_schema` call
    (not `CREATE VIEW IF NOT EXISTS`): a view holds no data, so re-running its
    definition is always safe, and `IF NOT EXISTS` silently froze a database created
    before a view-definition change on its old, wrong body forever -- e.g. a pre-
    2026-09-15 `marks_official` on a live database had no branch for CASHFLOW_USD /
    GAMMA / THETA / VEGA / RHO / DELTA_PA and the wrong (pre-2026-09-15/17) sources for
    PV_USD / DELTA / PREMIUM, and `create_schema` never corrected it because the view
    already existed. Recreating unconditionally re-syncs every view to the current
    definition on every app startup."""
    cases = "\n".join(
        f"      WHEN '{mt}' THEN '{src}'" for mt, src in OFFICIAL_MARK_SOURCE.items()
    )
    fallback_cases = "\n".join(
        f"      WHEN '{mt}' THEN '{src}'" for mt, src in OFFICIAL_FALLBACK_SOURCE.items()
    )
    # Second branch (2026-09-18): the OFFICIAL_FALLBACK_SOURCE row for a key that has no
    # primary-source row. NOT EXISTS keeps the key unique and lets a direct quote win.
    return f"""
DROP VIEW IF EXISTS marks_official;
CREATE VIEW marks_official AS
SELECT as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at
FROM marks
WHERE source = CASE mark_type
{cases}
    END
UNION ALL
SELECT f.as_of_date, f.instrument_id, f.settle_date, f.mark_type, f.value, f.source, f.snapped_at
FROM marks f
WHERE f.source = CASE f.mark_type
{fallback_cases}
    END
  AND NOT EXISTS (
    SELECT 1 FROM marks p
    WHERE p.as_of_date = f.as_of_date AND p.instrument_id = f.instrument_id
      AND p.settle_date = f.settle_date AND p.mark_type = f.mark_type
      AND p.source = CASE p.mark_type
{cases}
    END);

-- trades_official: a plain passthrough of `trades`, kept as a named view so every
-- engine query can read "the official trades" without caring whether that is ever
-- filtered again in future. Until 2026-09-17 this filtered out `source = 'BNP'` (the
-- once-daily BNP EOD snapshot, kept alongside the live blotter as a second trade
-- source, user decision 2026-09-16) to stop the same economic trade loaded from both
-- sources being summed twice into the ladder/delta/P&L (docs/open-questions.md item
-- 55). BNP was removed entirely 2026-09-17 ("no bnp fall back",
-- docs/bnp-excel-removal.md): nothing writes trades.source = 'BNP' any more, so that
-- filter became a no-op and was simplified away. A legacy source='BNP' row surviving
-- in an old database (before `purge_retired_sources` below has run) still surfaces
-- through this view unchanged -- never silently dropped by a view definition.
DROP VIEW IF EXISTS trades_official;
CREATE VIEW trades_official AS
SELECT * FROM trades;
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
  product             TEXT NOT NULL DEFAULT 'FX_FWD',   -- the trade's trades.product
  currency            TEXT NOT NULL,
  settle_date         TEXT NOT NULL,
  local_amount        REAL NOT NULL,
  usd_entry_amount    REAL NOT NULL,      -- nullable-by-sentinel 0.0: crosses/futures have no single USD
                                          -- entry leg, so 0.0 here means "not applicable", not "zero P&L"
  mark_type           TEXT NOT NULL DEFAULT 'SPOT',      -- which mark_type froze this row (SPOT, FUTURE_PX, ...)
  spot_usd_per_local  REAL NOT NULL,
  spot_as_of_date     TEXT NOT NULL,      -- date of the mark used to freeze (settle date, or last prior)
  spot_source         TEXT NOT NULL,
  pnl_usd             REAL NOT NULL,
  frozen_at           TEXT NOT NULL,
  note                TEXT NOT NULL       -- '' or 'spot dated <d> (last before settlement)'
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


# --------------------------------------------------------------------------- Bloomberg library
# What the trades on file need from Bloomberg for their P&L (data/bloomberg/library.py,
# user decision 2026-09-21): a pull asks for what is in here and nothing else. It changes
# only when the trades change -- the triggers below mark it out of date on any write to
# `trades` / `trade_legs`, and library.sync brings it up to date. Deliberately NO foreign
# key to `trades`: an upload deletes and rewrites the whole book before the sync runs.
BBG_LIBRARY_TABLES = ("bbg_library", "bbg_library_state")
_BBG_LIBRARY_DDL = """
CREATE TABLE IF NOT EXISTS bbg_library (
  trade_id        TEXT NOT NULL,
  kind            TEXT NOT NULL,      -- SPOT | FWD_OUTRIGHT | FUTURE_PX | OIS_CURVE | VOL_SMILE
  key             TEXT NOT NULL,      -- pair / future instrument_id; currency for OIS_CURVE
  settle_date     TEXT NOT NULL,      -- FWD_OUTRIGHT, FUTURE_PX: the date marked; '9999-12-31' otherwise
  bbg_ticker      TEXT NOT NULL,      -- the security asked for; '' where the kind stands for a set of them
  role            TEXT NOT NULL,      -- PAIR | CONVERSION (a USD-conversion pair's SPOT)
  product         TEXT NOT NULL,
  needed_from     TEXT NOT NULL,      -- the trade's trade_date
  needed_until    TEXT NOT NULL,      -- settle date / expiry / maturity: not asked for after it
  added_at        TEXT NOT NULL,
  PRIMARY KEY (trade_id, kind, key, settle_date)
);
CREATE TABLE IF NOT EXISTS bbg_library_state (
  id              INTEGER PRIMARY KEY CHECK (id = 1),
  dirty           INTEGER NOT NULL DEFAULT 1,   -- 1 = the trades changed since the last sync
  synced_at       TEXT NOT NULL DEFAULT '',
  code_version    TEXT NOT NULL DEFAULT ''      -- data/bloomberg/library.py::LIBRARY_VERSION that last synced it
                                               -- (2026-09-22: a kind the code learned since is a resync too)
);
INSERT OR IGNORE INTO bbg_library_state (id, dirty, synced_at) VALUES (1, 1, '');
""" + "".join(
    f"CREATE TRIGGER IF NOT EXISTS bbg_library_{table}_{name} AFTER {event} ON {table} "
    f"BEGIN UPDATE bbg_library_state SET dirty = 1; END;\n"
    for table in ("trades", "trade_legs") for name, event in (("ai", "INSERT"), ("au", "UPDATE"), ("ad", "DELETE")))


_CREATE_TABLE_RE = re.compile(r'CREATE TABLE IF NOT EXISTS\s+"?(\w+)"?\s*\((.*?)\n\);', re.DOTALL)
_CONSTRAINT_KEYWORDS = frozenset({"PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT"})


def _strip_sql_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def _split_top_level(body: str) -> List[str]:
    """Split a CREATE TABLE's column-list body on commas that aren't nested inside
    parens (e.g. ``PRIMARY KEY (a, b)``) or a single-quoted default string."""
    parts: List[str] = []
    depth = 0
    in_quote = False
    current: List[str] = []
    for ch in body:
        if ch == "'" :
            in_quote = not in_quote
        elif not in_quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append("".join(current))
                current = []
                continue
        current.append(ch)
    if current:
        parts.append("".join(current))
    return parts


def _parse_ddl_columns(ddl: str) -> Dict[str, List[Tuple[str, str]]]:
    """table_name -> [(column_name, column_definition_sql), ...] in declaration order,
    parsed straight out of every ``CREATE TABLE IF NOT EXISTS <name> (...)`` block in
    `ddl` -- the single source of truth for the schema, so a column added to the DDL
    here is automatically picked up by `_migrate_columns` with no second place to edit.
    Table-level constraint lines (``PRIMARY KEY (...)``, ``FOREIGN KEY (...)``, ...) are
    skipped: they name no column of their own (a column's own ``PRIMARY KEY`` /
    ``REFERENCES`` -- inline on its own column line, e.g. ``instrument_id TEXT PRIMARY
    KEY REFERENCES instruments`` -- is kept, since that line does start with a column
    name)."""
    out: Dict[str, List[Tuple[str, str]]] = {}
    for m in _CREATE_TABLE_RE.finditer(_strip_sql_comments(ddl)):
        table, body = m.group(1), m.group(2)
        columns: List[Tuple[str, str]] = []
        for frag in _split_top_level(body):
            frag = frag.strip()
            if not frag:
                continue
            name_m = re.match(r'^"([^"]+)"|^(\S+)', frag)
            if name_m is None:
                continue
            name = name_m.group(1) or name_m.group(2)
            if name.upper() in _CONSTRAINT_KEYWORDS:
                continue
            columns.append((name, frag[name_m.end():].strip()))
        out[table] = columns
    return out


def _migrate_columns(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a table's initial CREATE, for databases created by
    an older version of this module (or an older version of one of the DDL strings
    below). ``CREATE TABLE IF NOT EXISTS`` only helps brand new databases; an existing
    table needs an explicit ``ALTER TABLE ADD COLUMN`` per missing column, generically
    diffed here against ``PRAGMA table_info`` rather than a hand-maintained list that
    silently goes stale the next time a column is added to the DDL and this function is
    forgotten (as happened for `instrument_options.payoff`, 2026-09-17: the DDL declared
    it with a default from the start, but no one added it here, so every DB created
    before that DDL change was permanently missing it). Every column added via a
    generic ALTER here that is declared NOT NULL must carry a DEFAULT in its own DDL
    text -- SQLite refuses to ALTER TABLE ADD a NOT NULL column with no default -- which
    is already true of every column in this schema that has ever needed migrating onto
    an existing table (each has a documented sentinel: '' / 0 / 'VANILLA' / ...)."""
    # Every DDL block, the library's included (2026-09-22: bbg_library_state.code_version was
    # added and never reached an existing database while this list stopped at the bundles).
    ddl_tables = _parse_ddl_columns(_DDL + _LEDGER_DDL + _BUNDLES_DDL + _BBG_LIBRARY_DDL)
    for table, columns in ddl_tables.items():
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue  # table itself doesn't exist yet; the CREATE above will make it right
        for name, coldef in columns:
            if name not in existing:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN "{name}" {coldef}')


def create_schema(conn: sqlite3.Connection) -> None:
    """Create every table if absent (idempotent); drop and recreate every view
    unconditionally (see `_views_ddl`'s docstring for why); enable foreign keys."""
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executescript(_DDL + _views_ddl() + _LEDGER_DDL + _BUNDLES_DDL + _BBG_LIBRARY_DDL)
        _migrate_columns(conn)
        conn.commit()
    except sqlite3.OperationalError as exc:
        # A read-only connection (ui.app.connect_readonly, used by every tab callback)
        # cannot DROP/CREATE the views. The writable startup path (ui.app.ensure_schema)
        # has already brought the schema up to date, so a read-only caller simply
        # proceeds with what is there instead of failing (found 2026-09-17: the
        # Bundles sub-tab called this through a read-only handle).
        if "readonly" not in str(exc).lower() and "read-only" not in str(exc).lower():
            raise


# How long a connection waits for another writer before raising "database is locked".
# sqlite3's default is 5 s; the blotter upload holds its write lock for the whole backup +
# parse + load (data/ingest/upload.py), and the Bloomberg feed thread, the Market data
# tab's "Pull now" callback and the auto-backfill thread each write from their own
# connection -- with 5 s a pull landing during an upload was recorded as a Bloomberg
# failure ("pull failed: database is locked") although Bloomberg had answered
# (2026-09-18 audit). 60 s outlasts any upload without hiding a genuinely stuck writer.
BUSY_TIMEOUT_SECONDS = 60.0


def connect(path: Union[str, Path] = ":memory:", timeout: float = BUSY_TIMEOUT_SECONDS) -> sqlite3.Connection:
    """Open (or create) a database at `path` and apply the schema. `timeout` is the
    SQLite busy timeout (see BUSY_TIMEOUT_SECONDS)."""
    conn = sqlite3.connect(str(path), timeout=timeout)
    create_schema(conn)
    return conn


# --------------------------------------------------------------------- retired-source cleanup
def purge_retired_sources(conn: sqlite3.Connection) -> Dict[str, int]:
    """One-off, idempotent clean-up for a database created before the 2026-09-17 "no bnp
    fall back" removal (docs/bnp-excel-removal.md): deletes anything the retired BNP
    parser / workbook wrote, FK-safe (children before parents, since `connect` turns on
    `PRAGMA foreign_keys`), and drops the retired FX-swap rule's `swap_review` table
    (2026-09-24). Never deletes BBG_INTERP. Safe to call on a database that already has none of this --
    every step is a no-op then, and the counts returned are all zero.

    Called once per app startup from `ui/app.py::ensure_schema`. Does not touch
    `instruments` rows that only ever existed for a BNP-only trade (e.g. a stray
    `CASH-<ccy>` instrument or an IRS symbol with no surviving trade): harmless orphans,
    and safe deletion would require checking every other table for references first.
    """
    counts: Dict[str, int] = {}
    with conn:
        cur = conn.execute("DELETE FROM trade_legs WHERE trade_id IN (SELECT trade_id FROM trades WHERE source='BNP')")
        counts["trade_legs"] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        cur = conn.execute("DELETE FROM realised_pnl WHERE trade_id IN (SELECT trade_id FROM trades WHERE source='BNP')")
        counts["realised_pnl"] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        cur = conn.execute("DELETE FROM trades WHERE source='BNP'")
        counts["trades"] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        # BBG_INTERP was purged here until 2026-09-18; it is now the live feed's official
        # FWD_OUTRIGHT fallback (OFFICIAL_FALLBACK_SOURCE above), so deleting it at startup
        # would wipe every broken-date forward and the backfilled history on each launch.
        cur = conn.execute("DELETE FROM marks WHERE source IN ('BNP_BVAL','WORKBOOK_REFERENCE')")
        counts["marks"] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        had_positions = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='positions'"
        ).fetchone()[0]
        if had_positions:
            counts["positions_table_dropped"] = 1
            conn.execute("DROP TABLE IF EXISTS positions")
        else:
            counts["positions_table_dropped"] = 0
        # swap_review (2026-09-24): the retired FX-swap package rule's ambiguous candidates.
        # Its foreign key to `trades` would block deleting a trade it names, and nothing
        # writes or reads it any more. The only table dropped on an existing database for
        # that removal; index_fixings / irs_direction_overrides are left as they are.
        had_swap_review = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='swap_review'"
        ).fetchone()[0]
        counts["swap_review_table_dropped"] = 1 if had_swap_review else 0
        if had_swap_review:
            conn.execute("DROP TABLE IF EXISTS swap_review")
    return counts
