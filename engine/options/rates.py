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
  4. Covered interest parity, implied from the PAIR's own official forward
     and spot plus the OTHER currency's already-resolved rate (steps 1-3
     for that other currency) -- ``source_kind == IMPLIED_FORWARD``. Only
     tried per-currency (never both legs at once: if neither currency
     resolves 1-3, there is nothing to imply from and the pair skips as
     before). See "Implied-forward fallback" below.
  5. Neither -- skip, reason ``"no curve/rate <CCY>"``.

This is a genuine, currently-unclosable gap for SEK/NOK specifically (not a
bug in this module): both currencies' central banks publish overnight rates
(SWESTR, NOWA) that ``engine/rates/conventions.py::CCY_RFR`` simply does not
carry yet -- see that module's own docstring for how to extend it, out of
this package's ownership (``engine/rates/`` belongs to rates-pricer). A
Bloomberg deposit-rate feed to auto-populate ``manual_rates`` is a further
follow-up, not built here -- this phase only adds the manual entry point and
its resolution order.

**Implied-forward fallback (Phase 7.2, 2026-09-18).** SEK (and NOK/TWD/ZAR)
have no OIS convention, and the user does not want to hand-enter a
``manual_rates`` row for every one of them -- the live Bloomberg pull
already writes an official ``FWD_OUTRIGHT`` for EURSEK (and its own SPOT),
so the missing rate can be derived instead of asked for. Covered interest
parity for pair BASE/QUOTE (domestic = quote, foreign = base, this module's
own convention throughout) with spot ``S``, forward ``F`` to time ``T``
(plain ACT/365 from ``as_of`` to the option's expiry -- deliberately not
this package's calendar-aware Business252 day count used elsewhere, since
this is a closed-form rearrangement of the forward-pricing identity itself,
not a discounting day-count choice):

    r_quote = r_base  + ln(F/S) / T
    r_base  = r_quote - ln(F/S) / T

Only invoked for a currency that resolves NEITHER an OIS curve NOR a manual
rate (steps 1-3 above) -- an existing curve or manual rate is never
overridden by an implied one, so precedence is exactly OIS_CURVE >
MANUAL_EXPIRY / MANUAL_FLAT > IMPLIED_FORWARD. ``F`` is read via
``_forward_for_expiry``: an exact ``marks_official`` ``FWD_OUTRIGHT`` for
the option's own expiry date if one exists, else linear interpolation in
forward points between the two bracketing ``FWD_OUTRIGHT`` marks (mirrors
CLAUDE.md's own ``BBG_INTERP`` convention), else linear extrapolation from
the nearest two marks -- but capped at one more "last tenor gap" beyond the
final point in that direction, never further (a lone point with nothing to
interpolate or extrapolate against, or an expiry beyond that one-gap cap,
returns ``None``). Rejects (returns ``None``, so the caller keeps its
PRE-EXISTING ``"no curve/rate <CCY>"`` skip reason -- this fallback never
invents its own reason string) when: fewer than two ``FWD_OUTRIGHT`` marks
are staged for the pair, the expiry falls outside the bracket-or-capped-
extrapolation window, the pair's SPOT is missing, or ``T <= 0``.
``RateInput.detail`` records provenance for diagnostics, e.g. ``"implied
from EURSEK forward 2026-11-25 and EUR ESTR curve"``.

**Never falls back to a placeholder.** No ``curve_quotes`` rows, no
``manual_rates`` row, and no implied-forward rate for a required currency on
``as_of`` -> ``resolve_fx_rates`` / ``resolve_ccy_rate`` return
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
IMPLIED_FORWARD = "IMPLIED_FORWARD"


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
    source_kind: str  # OIS_CURVE | MANUAL_EXPIRY | MANUAL_FLAT | IMPLIED_FORWARD
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
        index = CCY_RFR.get(ccy, "OIS")
        detail = f"{ccy} {index} curve"
        # A CurveSet built by a fallback bootstrap (engine/rates/curves.py records it as
        # `bootstrap_note`, e.g. "USD SOFR bootstrap 2026-09-22: log-cubic did not
        # converge (...); log-linear used") names that fallback in the provenance, so a
        # priced option's rate says which curve it really came from. getattr: works
        # whether or not the CurveSet carries the attribute at all.
        note = getattr(curve_set, "bootstrap_note", "") or ""
        if note:
            detail = f"{detail}; {note}"
        return RateInput(rate, OIS_CURVE, detail=detail), ""

    manual_rate, source_kind = _get_manual_rate(conn, as_of, ccy, expiry_iso)
    if manual_rate is not None:
        detail = f"{ccy} manual_rates " + ("exact expiry" if source_kind == MANUAL_EXPIRY else "flat '*'")
        return RateInput(manual_rate, source_kind, detail=detail), ""

    return None, f"no curve/rate {ccy}"


# --------------------------------------------------------------- implied-forward fallback (Phase 7.2, 2026-09-18)
# Covered interest parity -- see module docstring's "Implied-forward fallback" section for
# the formula, precedence and reject conditions.

def _get_pair_spot(conn: sqlite3.Connection, as_of: str, pair: str) -> Optional[float]:
    """Duplicated from `inputs.py::get_spot` rather than imported -- same
    "small private query duplicated, not cross-imported" pattern this module
    already uses for `_read_curve_quotes` (its own docstring explains why:
    avoids a module-level import cycle with `inputs.py`, which lazily
    imports THIS module inside a function body)."""
    row = conn.execute(
        "SELECT value FROM marks_official WHERE as_of_date = ? AND instrument_id = ? AND mark_type = 'SPOT'",
        (as_of, pair),
    ).fetchone()
    return row[0] if row is not None else None


def _get_fwd_outright_points(conn: sqlite3.Connection, as_of: str, pair: str):
    """Every official FWD_OUTRIGHT mark for `pair` on `as_of`, as
    `(settle_date, value)` pairs sorted by date -- the raw material for
    `_forward_for_expiry`."""
    rows = conn.execute(
        "SELECT settle_date, value FROM marks_official WHERE as_of_date = ? AND instrument_id = ? "
        "AND mark_type = 'FWD_OUTRIGHT'",
        (as_of, pair),
    ).fetchall()
    return sorted((datetime.date.fromisoformat(d), v) for d, v in rows)


def _forward_for_expiry(
    conn: sqlite3.Connection, as_of: str, pair: str, expiry_date: datetime.date
) -> Optional[float]:
    """The pair's forward outright to `expiry_date`: an exact FWD_OUTRIGHT
    mark if one exists; else linear interpolation in forward points between
    the two bracketing marks (mirrors CLAUDE.md's own BBG_INTERP
    convention); else linear extrapolation from the nearest two marks,
    capped at one more "last tenor gap" beyond the final point in that
    direction -- never further. None if fewer than two marks are staged, or
    the target falls outside both the bracket and that capped-extrapolation
    window."""
    points = _get_fwd_outright_points(conn, as_of, pair)
    for d, v in points:
        if d == expiry_date:
            return v
    if len(points) < 2:
        return None

    before = [(d, v) for d, v in points if d < expiry_date]
    after = [(d, v) for d, v in points if d > expiry_date]

    if before and after:
        d0, v0 = before[-1]
        d1, v1 = after[0]
        w = (expiry_date - d0).days / (d1 - d0).days
        return v0 + w * (v1 - v0)

    if before and not after:
        d0, v0 = points[-2]
        d1, v1 = points[-1]
        gap = (d1 - d0).days
        if gap <= 0 or (expiry_date - d1).days > gap:
            return None
        w = (expiry_date - d0).days / gap
        return v0 + w * (v1 - v0)

    if after and not before:
        d0, v0 = points[0]
        d1, v1 = points[1]
        gap = (d1 - d0).days
        if gap <= 0 or (d0 - expiry_date).days > gap:
            return None
        w = (expiry_date - d0).days / gap
        return v0 + w * (v1 - v0)

    return None


def _imply_rate_from_forward(
    conn: sqlite3.Connection,
    as_of: str,
    pair: str,
    expiry_iso: str,
    known_input: RateInput,
    solve_for: str,
) -> Optional[RateInput]:
    """Covered interest parity: given the OTHER currency's already-resolved
    `known_input`, imply this currency's rate from the pair's own official
    spot + forward. `solve_for` is 'domestic' or 'foreign'. Returns None on
    any reject condition (missing/non-positive spot, missing/non-positive
    forward, `T <= 0`) -- the caller keeps its own pre-existing
    "no curve/rate <CCY>" skip reason in that case, this function never
    invents its own reason string (see module docstring)."""
    spot = _get_pair_spot(conn, as_of, pair)
    if spot is None or spot <= 0:
        return None

    as_of_date = datetime.date.fromisoformat(as_of)
    expiry_date = datetime.date.fromisoformat(expiry_iso)
    t_years = (expiry_date - as_of_date).days / 365.0
    if t_years <= 0:
        return None

    forward = _forward_for_expiry(conn, as_of, pair, expiry_date)
    if forward is None or forward <= 0:
        return None

    import math
    ln_fs = math.log(forward / spot)
    if solve_for == "domestic":
        rate = known_input.rate + ln_fs / t_years
    elif solve_for == "foreign":
        rate = known_input.rate - ln_fs / t_years
    else:
        raise ValueError(f"solve_for must be 'domestic' or 'foreign', got {solve_for!r}")

    detail = f"implied from {pair} forward {expiry_iso} and {known_input.detail}"
    return RateInput(rate, IMPLIED_FORWARD, detail=detail)


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
    first, then manual_rates (`resolve_ccy_rate_with_source`); if exactly
    one currency resolves neither and the OTHER one does, covered interest
    parity off the pair's own forward + spot is tried as a last resort
    before giving up (`_imply_rate_from_forward` -- see module docstring's
    "Implied-forward fallback" section). An existing curve or manual rate is
    never overridden by an implied one.

    Returns (FxRates, "") on success, or (None, reason) when a currency
    resolves neither a curve, a manual rate, nor an implied-forward rate --
    never falls back to a placeholder rate. `reason` is always the plain
    per-currency "no curve/rate <CCY>" message (never a CIP-specific one),
    so a still-unpriceable option's skip reason is unchanged by this
    fallback existing.
    """
    if len(pair) != 6:
        return None, f"pair {pair!r} is not a plain 6-char base+quote instrument_id"
    foreign_ccy, domestic_ccy = pair[:3], pair[3:]

    foreign_input, foreign_reason = resolve_ccy_rate_with_source(conn, as_of, foreign_ccy, expiry_iso, curve_cache)
    domestic_input, domestic_reason = resolve_ccy_rate_with_source(conn, as_of, domestic_ccy, expiry_iso, curve_cache)

    if foreign_input is None and domestic_input is None:
        return None, foreign_reason

    if foreign_input is None:
        implied = _imply_rate_from_forward(conn, as_of, pair, expiry_iso, domestic_input, solve_for="foreign")
        if implied is None:
            return None, foreign_reason
        foreign_input = implied
    elif domestic_input is None:
        implied = _imply_rate_from_forward(conn, as_of, pair, expiry_iso, foreign_input, solve_for="domestic")
        if implied is None:
            return None, domestic_reason
        domestic_input = implied

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
