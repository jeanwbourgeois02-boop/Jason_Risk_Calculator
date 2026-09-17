"""Real OIS-curve rates for FX option pricing (Phase 7, options_calc merge).

Replaces ``options_calc.fx.rate_curves``' illustrative placeholder table as
the source of the Garman-Kohlhagen ``domestic_rate`` / ``foreign_rate``
inputs. ``inputs.py`` no longer imports that vendored module for real
pricing -- the illustrative fallback is deleted from this package's own
input-resolution path (the vendored ``fx/rate_curves.py`` file itself is
untouched, per this package's "never edit vendor/" rule; only stops being
called).

**Exactness, stated plainly.** A vanilla FX option only ever needs a
domestic and a foreign discount factor to one date -- its own expiry. The
continuously-compounded zero rate read off a bootstrapped OIS discount
curve to that exact date, ``-ln(DF(T)) / T``, is therefore EXACT for
Garman-Kohlhagen discounting: no interpolation-vs-model-choice
approximation is introduced by using a real curve instead of a single flat
number, unlike (say) using it for a swap's full cashflow schedule.

**Domestic vs foreign, restated (matches pricer.py's own docstring):**
domestic = quote currency (``pair[3:]``), foreign = base currency
(``pair[:3]``).

**Which currencies are supported.** First choice is always the OIS curve:
currencies with an OIS convention in ``engine/rates/conventions.py::CCY_RFR``
(Phase 1 scope: USD SOFR, EUR ESTR, GBP SONIA, JPY TONA, CHF SARON, CAD
CORRA, AUD AONIA -- seven currencies, NOT the full G10 set
``options_calc.fx.g10`` covers) resolve a real bootstrapped zero rate, and
any ``manual_rates`` row for that currency is ignored -- the curve always
wins when it exists (Phase 7.1, 2026-09-17).

**manual_rates fallback (Phase 7.1, 2026-09-17).** The book's biggest FX
option positions are in pairs like EURSEK, USDTWD and USDZAR -- SEK/TWD/ZAR
have no OIS convention anywhere in this codebase, so deleting the
illustrative placeholder (see below) without a fallback would silently skip
every one of those real positions, not just an illustrative test fixture.
``manual_rates`` (this module's own small DDL, same shape as ``inputs.py``'s
``option_vols``) lets a currency with no OIS curve get an explicit,
human-entered continuously-compounded rate instead -- never a default,
never invented by this module. Resolution order per currency, used by both
``resolve_fx_rates`` (per-leg) and ``resolve_ccy_rate`` (single-currency):

  1. OIS curve, if the currency has a ``CCY_RFR`` entry AND has
     ``curve_quotes`` rows on ``as_of`` -- ``RateInput.source_kind ==
     OIS_CURVE``.
  2. ``manual_rates`` row for the option's own exact expiry --
     ``source_kind == MANUAL_EXPIRY``.
  3. ``manual_rates`` flat ``'*'`` row for the currency --
     ``source_kind == MANUAL_FLAT``.
  4. Neither -- skip, reason ``"no curve/rate <CCY>"``.

This is a genuine, currently-unclosable gap for SEK/NOK specifically (not a
bug in this module): both currencies' central banks publish overnight rates
(SWESTR, NOWA) that ``engine/rates/conventions.py::CCY_RFR`` simply does not
carry yet -- see that module's own docstring for how to extend it, out of
this package's ownership (``engine/rates/`` belongs to rates-pricer). A
Bloomberg deposit-rate feed to auto-populate ``manual_rates`` is a further
follow-up, not built here -- this phase only adds the manual entry point and
its resolution order.

**Never falls back to a placeholder.** No ``curve_quotes`` rows AND no
``manual_rates`` row for a required currency on ``as_of`` ->
``resolve_fx_rates`` / ``resolve_ccy_rate`` return
``(None, "no curve/rate <CCY>")``. Nothing here ever reads
``options_calc.fx.rate_curves.get_rate`` or ships a made-up number.

**Caching.** ``curve_cache`` is a plain dict, keyed ``(as_of, ccy)``,
that the caller (``inputs.py`` / ``store.py``) owns for one
``price_and_store`` / ``price_all_and_store`` run -- the same
build-once-per-run pattern ``inputs.py::_cached_surface`` already uses for
vol surfaces, so N option trades sharing a currency don't each rebuild
that currency's OIS bootstrap.

**Evaluation-date quirk.** QuantLib's global ``evaluationDate`` is mutable
process-wide state. The vendored ``options_calc`` FX engines reset it to
the REAL system date on every pricer call (see pricer.py's own "Time-to-
expiry" docstring section) -- so a curve's ``zeroRate()`` read AFTER any
vendored pricer has run in between can silently use the wrong date unless
reset first. ``zero_rate_to`` resets it to ``as_of`` immediately before
reading, mirroring ``engine/rates_vol/inputs.py::_set_eval_date``.
"""
from __future__ import annotations

import datetime
import sqlite3
from dataclasses import dataclass
from typing import Optional, Tuple

from engine.rates.conventions import CCY_RFR
from engine.rates.curves import CurveSet, build_curve_set

# ------------------------------------------------------------------- manual_rates store

_MANUAL_RATES_DDL = """
CREATE TABLE IF NOT EXISTS manual_rates (
  as_of_date       TEXT NOT NULL,
  ccy              TEXT NOT NULL,
  rate             REAL NOT NULL,   -- continuously-compounded decimal
  tenor_or_expiry  TEXT NOT NULL,   -- an ISO expiry date, or '*' for a flat ccy-wide fallback
  source           TEXT NOT NULL,
  PRIMARY KEY (as_of_date, ccy, tenor_or_expiry, source)
);
"""

FLAT_EXPIRY = "*"

# RateInput.source_kind values.
OIS_CURVE = "OIS_CURVE"
MANUAL_EXPIRY = "MANUAL_EXPIRY"
MANUAL_FLAT = "MANUAL_FLAT"


def ensure_manual_rates_table(conn: sqlite3.Connection) -> None:
    """Create ``manual_rates`` if absent -- same defensive pattern as
    ``inputs.py::ensure_option_vols_table``."""
    conn.execute(_MANUAL_RATES_DDL)


def set_manual_rate(
    conn: sqlite3.Connection,
    as_of: str,
    ccy: str,
    rate: float,
    expiry: str = FLAT_EXPIRY,
    source: str = "MANUAL",
) -> None:
    """Write one manual continuously-compounded rate for a currency with no
    OIS convention (or one simply missing curve_quotes for the day).
    `expiry` is either the option's exact ISO expiry date (an
    expiry-specific quote) or FLAT_EXPIRY ('*') for a ccy-wide fallback used
    when no expiry-specific quote exists. Ignored entirely for a currency
    whose OIS curve resolves -- see module docstring's resolution order."""
    ensure_manual_rates_table(conn)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO manual_rates (as_of_date, ccy, rate, tenor_or_expiry, source) "
            "VALUES (?,?,?,?,?)",
            (as_of, ccy, float(rate), expiry, source),
        )


def _get_manual_rate(
    conn: sqlite3.Connection, as_of: str, ccy: str, expiry_iso: str, source: str = "MANUAL"
):
    """Exact (as_of, ccy, expiry) match first, else the ccy's flat '*' entry
    for the same (as_of, source); else (None, None)."""
    ensure_manual_rates_table(conn)
    row = conn.execute(
        "SELECT rate FROM manual_rates WHERE as_of_date = ? AND ccy = ? AND tenor_or_expiry = ? AND source = ?",
        (as_of, ccy, expiry_iso, source),
    ).fetchone()
    if row is not None:
        return row[0], MANUAL_EXPIRY
    row = conn.execute(
        "SELECT rate FROM manual_rates WHERE as_of_date = ? AND ccy = ? AND tenor_or_expiry = ? AND source = ?",
        (as_of, ccy, FLAT_EXPIRY, source),
    ).fetchone()
    if row is not None:
        return row[0], MANUAL_FLAT
    return None, None


def _read_curve_quotes(conn: sqlite3.Connection, as_of: str, ccy: str, index: str):
    """Same source-preference rule as ``engine/rates/store.py::_read_curve_quotes``
    (prefer 'BBG_BDP', else whichever source sorts first alphabetically) --
    duplicated rather than imported, since that function is private
    (leading underscore) to its own module, not part of engine/rates'
    public interface."""
    rows = conn.execute(
        'SELECT tenor, value, source FROM curve_quotes '
        'WHERE as_of_date = ? AND ccy = ? AND "index" = ? AND quote_type = ?',
        (as_of, ccy, index, "OIS"),
    ).fetchall()
    if not rows:
        return []
    sources = sorted({r[2] for r in rows})
    source = "BBG_BDP" if "BBG_BDP" in sources else sources[0]
    return [(tenor, value) for tenor, value, src in rows if src == source]


def _set_eval_date(as_of_date: datetime.date) -> None:
    import QuantLib as ql

    ql.Settings.instance().evaluationDate = ql.Date(as_of_date.day, as_of_date.month, as_of_date.year)


def _build_curve_for_ccy(
    conn: sqlite3.Connection, as_of: str, ccy: str
) -> Tuple[Optional[CurveSet], str]:
    """Bootstrap (never store -- writing `curves` rows stays
    engine/rates/store.py::bootstrap_and_store's job; this package only
    reads curve_quotes to get an in-memory CurveSet) the OIS curve for
    `ccy` as of `as_of`. Returns (None, "no curve <CCY>") if `ccy` has no
    OIS convention or no curve_quotes rows on that date."""
    index = CCY_RFR.get(ccy)
    if index is None:
        return None, f"no curve {ccy}"
    quotes = _read_curve_quotes(conn, as_of, ccy, index)
    if not quotes:
        return None, f"no curve {ccy}"
    as_of_date = datetime.date.fromisoformat(as_of)
    return build_curve_set(quotes, as_of_date, ccy, index), ""


def _get_curve(
    conn: sqlite3.Connection, as_of: str, ccy: str, curve_cache: Optional[dict]
) -> Tuple[Optional[CurveSet], str]:
    if curve_cache is None:
        return _build_curve_for_ccy(conn, as_of, ccy)
    key = (as_of, ccy)
    if key not in curve_cache:
        curve_cache[key] = _build_curve_for_ccy(conn, as_of, ccy)
    return curve_cache[key]


def zero_rate_to(curve_set: CurveSet, as_of_date: datetime.date, target_date: datetime.date) -> float:
    """Continuously-compounded zero rate off `curve_set`'s bootstrapped
    discount curve, from `as_of_date` to `target_date` (ql.Actual365Fixed(),
    matching every other day count this package uses). Resets QuantLib's
    evaluationDate to `as_of_date` first -- see module docstring."""
    import QuantLib as ql

    _set_eval_date(as_of_date)
    target_ql = ql.Date(target_date.day, target_date.month, target_date.year)
    return curve_set.discount_curve.zeroRate(target_ql, ql.Actual365Fixed(), ql.Continuous).rate()


@dataclass
class RateInput:
    """One resolved currency rate plus WHERE it came from -- see module
    docstring's resolution-order list. Mirrors `inputs.py::VolInput`'s
    provenance pattern."""
    rate: float
    source_kind: str  # OIS_CURVE | MANUAL_EXPIRY | MANUAL_FLAT
    detail: str = ""


@dataclass
class FxRates:
    domestic_rate: float
    foreign_rate: float
    domestic_ccy: str
    foreign_ccy: str
    # Provenance of each leg's rate (Phase 7.1, 2026-09-17) -- None only for
    # an FxRates built by test/legacy code that bypasses resolve_fx_rates
    # entirely; every value resolve_fx_rates itself returns sets both.
    domestic_rate_source: Optional[RateInput] = None
    foreign_rate_source: Optional[RateInput] = None


def resolve_ccy_rate_with_source(
    conn: sqlite3.Connection,
    as_of: str,
    ccy: str,
    expiry_iso: str,
    curve_cache: Optional[dict] = None,
) -> Tuple[Optional[RateInput], str]:
    """Single-currency rate resolution with provenance -- the shared
    implementation behind both `resolve_fx_rates` (per FX leg) and
    `resolve_ccy_rate` (single-rate equity/commodity callers). See module
    docstring's numbered resolution order. Returns (RateInput, "") or
    (None, "no curve/rate <CCY>") -- never a placeholder."""
    curve_set, _reason = _get_curve(conn, as_of, ccy, curve_cache)
    if curve_set is not None:
        as_of_date = datetime.date.fromisoformat(as_of)
        expiry_date = datetime.date.fromisoformat(expiry_iso)
        rate = zero_rate_to(curve_set, as_of_date, expiry_date)
        return RateInput(rate, OIS_CURVE, detail=f"{ccy} OIS curve"), ""

    manual_rate, source_kind = _get_manual_rate(conn, as_of, ccy, expiry_iso)
    if manual_rate is not None:
        detail = f"{ccy} manual_rates " + ("exact expiry" if source_kind == MANUAL_EXPIRY else "flat '*'")
        return RateInput(manual_rate, source_kind, detail=detail), ""

    return None, f"no curve/rate {ccy}"


def resolve_fx_rates(
    conn: sqlite3.Connection,
    as_of: str,
    pair: str,
    expiry_iso: str,
    curve_cache: Optional[dict] = None,
) -> Tuple[Optional[FxRates], str]:
    """Resolve (domestic_rate, foreign_rate) for `pair`'s option expiry.
    domestic = quote ccy (`pair[3:]`), foreign = base ccy (`pair[:3]`) --
    matches pricer.py's own Garman-Kohlhagen convention and
    options_calc.fx.g10.domestic_and_foreign_currency's orientation, but
    resolved from this schema's plain 6-char pair id directly rather than
    via g10.py's lookup table, so it works for ANY pair (not just the 45
    recognized G10 ones). Each currency independently tries its OIS curve
    first, then manual_rates -- see module docstring's resolution order and
    `resolve_ccy_rate_with_source`.

    Returns (FxRates, "") on success, or (None, reason) on the first
    currency that resolves neither a curve nor a manual rate -- never falls
    back to a placeholder rate.
    """
    if len(pair) != 6:
        return None, f"pair {pair!r} is not a plain 6-char base+quote instrument_id"
    foreign_ccy, domestic_ccy = pair[:3], pair[3:]

    foreign_input, reason = resolve_ccy_rate_with_source(conn, as_of, foreign_ccy, expiry_iso, curve_cache)
    if foreign_input is None:
        return None, reason
    domestic_input, reason = resolve_ccy_rate_with_source(conn, as_of, domestic_ccy, expiry_iso, curve_cache)
    if domestic_input is None:
        return None, reason

    return FxRates(
        domestic_input.rate, foreign_input.rate, domestic_ccy, foreign_ccy,
        domestic_rate_source=domestic_input, foreign_rate_source=foreign_input,
    ), ""


def resolve_ccy_rate(
    conn: sqlite3.Connection,
    as_of: str,
    ccy: str,
    expiry_iso: str,
    curve_cache: Optional[dict] = None,
) -> Tuple[Optional[float], str]:
    """Single-currency zero rate to `expiry_iso` -- used by the equity/
    commodity pricers (one discount rate, `instruments.quote_ccy`, rather
    than a domestic/foreign pair). Public signature unchanged (still a
    plain float, not RateInput) -- `equity_commodity.py` consumes the
    return value directly as a number; use
    `resolve_ccy_rate_with_source` for provenance."""
    rate_input, reason = resolve_ccy_rate_with_source(conn, as_of, ccy, expiry_iso, curve_cache)
    if rate_input is None:
        return None, reason
    return rate_input.rate, ""
