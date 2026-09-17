"""Market-input resolution for engine/options pricers: spot, rates, vol.

Every input here is resolved with the same discipline as the rest of this
app -- missing stays missing, nothing is defaulted or fabricated. A trade
that cannot be priced comes back as a skip reason (see ``store.py``), never
a guessed number.

- **SPOT**: read from ``marks_official`` (never ``marks`` directly, per
  CLAUDE.md "Official marks" -- official source for SPOT is
  ``BBG_BFXFORWARD``), keyed by the PLAIN PAIR instrument_id (e.g.
  'EURSEK' -- the ordinary FX instrument row, base_ccy+quote_ccy). This is
  NOT the option's own instrument_id (which encodes strike/expiry/side,
  e.g. 'EURSEK092326C-197727826', and has no SPOT mark of its own).
  Missing -> ``get_spot`` returns ``None``.

- **Rates**: ``options_calc.fx.rate_curves.get_domestic_and_foreign_rates``.
  LOUDLY NOT LIVE DATA -- see that module's own docstring: these are
  plausible-looking illustrative placeholders for each G10 currency's
  short-term rate, not a snapshot of any real date. A future phase
  (Phase 5/7 of the options_calc merge plan, ``docs/open-questions.md``
  item 61) replaces this with a real curve/rate feed; nothing computed with
  today's rates should be read as production-accurate P&L. Only the 45
  recognized G10 pairs (``options_calc.fx.g10``) are supported; any other
  pair currency raises inside the vendored lookup, caught here and turned
  into a skip.

- **Vol**: this package's own ``option_vols`` table -- a flat, manually
  entered placeholder (this phase only; Phase 5 adds a live vol feed /
  delta-vol surface, see engine/options/__init__.py's scope ledger). Looked
  up by the option's own exact expiry first, falling back to the pair's
  '*' (flat, tenor-agnostic) entry, else ``None`` -- never defaulted to an
  arbitrary number.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

# ------------------------------------------------------------------- vol store

_OPTION_VOLS_DDL = """
CREATE TABLE IF NOT EXISTS option_vols (
  as_of_date      TEXT NOT NULL,
  pair            TEXT NOT NULL,
  tenor_or_expiry TEXT NOT NULL,   -- an ISO expiry date, or '*' for a flat pair-wide fallback
  vol             REAL NOT NULL,
  source          TEXT NOT NULL,
  PRIMARY KEY (as_of_date, pair, tenor_or_expiry, source)
);
"""

FLAT_TENOR = "*"


def ensure_option_vols_table(conn: sqlite3.Connection) -> None:
    """Create ``option_vols`` if absent -- same defensive pattern as
    ``data/bloomberg/rates_marketdata.py::ensure_curve_quotes_table``."""
    conn.execute(_OPTION_VOLS_DDL)


def set_manual_vol(
    conn: sqlite3.Connection,
    as_of: str,
    pair: str,
    tenor_or_expiry: str,
    vol: float,
    source: str = "MANUAL",
) -> None:
    """Write one manual vol quote. `tenor_or_expiry` is either the option's
    exact ISO expiry date (an expiry-specific quote) or FLAT_TENOR ('*') for
    a pair-wide fallback used when no expiry-specific quote exists."""
    ensure_option_vols_table(conn)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO option_vols (as_of_date, pair, tenor_or_expiry, vol, source) "
            "VALUES (?,?,?,?,?)",
            (as_of, pair, tenor_or_expiry, float(vol), source),
        )


def get_manual_vol(
    conn: sqlite3.Connection,
    as_of: str,
    pair: str,
    expiry_iso: str,
    source: str = "MANUAL",
) -> Optional[float]:
    """Exact (as_of, pair, expiry) match first; else the pair's flat '*'
    entry for the same (as_of, source); else None."""
    ensure_option_vols_table(conn)
    row = conn.execute(
        "SELECT vol FROM option_vols WHERE as_of_date = ? AND pair = ? AND tenor_or_expiry = ? AND source = ?",
        (as_of, pair, expiry_iso, source),
    ).fetchone()
    if row is not None:
        return row[0]
    row = conn.execute(
        "SELECT vol FROM option_vols WHERE as_of_date = ? AND pair = ? AND tenor_or_expiry = ? AND source = ?",
        (as_of, pair, FLAT_TENOR, source),
    ).fetchone()
    return row[0] if row is not None else None


# ------------------------------------------------------------------- spot

def get_spot(conn: sqlite3.Connection, as_of: str, pair: str) -> Optional[float]:
    """SPOT mark for `pair` (the plain 6-char FX instrument_id) from
    marks_official as of `as_of`. None if absent -- never defaulted."""
    row = conn.execute(
        "SELECT value FROM marks_official WHERE as_of_date = ? AND instrument_id = ? AND mark_type = 'SPOT'",
        (as_of, pair),
    ).fetchone()
    return row[0] if row is not None else None


# ------------------------------------------------------------------- combined resolution

@dataclass
class MarketInputs:
    spot: float
    domestic_rate: float
    foreign_rate: float
    vol: float


@dataclass
class InputsResult:
    inputs: Optional[MarketInputs]
    reason: str = ""  # '' when inputs is not None


def resolve_market_inputs(conn: sqlite3.Connection, as_of: str, pair: str, expiry_iso: str) -> InputsResult:
    """Resolve spot/rates/vol for one (as_of, pair, expiry). Returns
    InputsResult(None, reason) on the first missing input -- callers
    (store.py) turn that straight into a skipped PricingOutcome."""
    spot = get_spot(conn, as_of, pair)
    if spot is None:
        return InputsResult(None, "no SPOT mark")

    from .vendor.options_calc.fx.rate_curves import get_domestic_and_foreign_rates

    try:
        domestic_rate, foreign_rate = get_domestic_and_foreign_rates(pair)
    except ValueError:
        return InputsResult(None, f"no rate data for pair {pair!r} (not a recognized G10 pair)")

    vol = get_manual_vol(conn, as_of, pair, expiry_iso)
    if vol is None:
        return InputsResult(None, "no vol")

    return InputsResult(MarketInputs(spot=spot, domestic_rate=domestic_rate, foreign_rate=foreign_rate, vol=vol))
