"""Glue between this package's ported QuantLib pricing core and the repository's
SQLite schema (``data/ingest/schema.py``): reads ``curve_quotes`` / ``trades`` /
``trade_legs``, writes ``curves`` / ``marks``.

Four entry points:
  - ``bootstrap_and_store(conn, as_of, ccy, index=None)``: bootstraps the OIS curve for
    (ccy, index) from ``curve_quotes`` and writes one ``curves`` row per pillar,
    ``source='QL_PRICER'``. Returns the in-memory ``CurveSet`` (used by
    ``price_and_store`` so DV01 bucket bumping has live ``ql.SimpleQuote`` objects to
    perturb, not just the frozen discount factors written to ``curves``).
  - ``price_and_store(conn, as_of, trade_id)``: prices one IRS trade and writes
    ``PV_USD`` / ``DV01_USD`` / ``PAR_RATE`` marks, ``source='QL_PRICER'``.
  - ``recalc_on_file(conn, as_of, since=None)`` (2026-09-22): re-prices every IRS from
    the ``curve_quotes`` on file, day by day, asking Bloomberg nothing; the rerun of
    the past days after the switch to flat forwards (``curves.py``), and the launcher's
    / the pull button's no-Bloomberg path.
  - ``reverse_direction_marks(conn, trade_id, instrument_id)`` (2026-09-22): when a
    swap's pay / receive direction is flipped (``data/ingest/irs_direction.py``), this
    package's own ``PV_USD`` / ``DV01_USD`` / ``CASHFLOW_USD`` rows are multiplied by
    -1 on every date, exact for a vanilla OIS (below). It lives here and not in the
    ingest layer because hard rule 2 says nothing reaches ``marks`` that the app's own
    pricers did not produce: the pricer reverses its own marks (reviewer finding,
    2026-09-22). Imports QuantLib at module load like the rest of this module, so the
    ingest layer imports it lazily, inside the function that calls it.

Sign / value conventions (see also ``valuation.py`` and ``instruments.py``
docstrings, and CLAUDE.md "P&L conventions"):
  - ``trades.quantity > 0`` -> pay fixed (CLAUDE.md, matches ``data/ingest/irs.py``'s
    documented convention: BNP ``Position > 0`` -> pay fixed). ``notional`` passed to
    the pricer is ``abs(quantity)``.
  - ``PV_USD`` = ``SwapResult.npv`` (QuantLib's own Payer/Receiver-signed NPV, positive
    = asset to the fund). **Caveat**: Phase 1 scope has no FX-spot source wired in, so
    for a non-USD-notional IRS this is actually PV in the swap's own notional currency,
    not converted to USD -- every IRS in the current reference data
    (``data/raw/HA_PNL_20260818.csv``) is USD notional (`IRSOIS-USD-...`), so this does
    not bite yet, but it is a known gap flagged in agent memory
    (``rates-fx-conversion-gap.md``), not silently assumed away.
  - ``DV01_USD`` = ``SwapResult.dv01_parallel`` (NPV change for a +1bp parallel curve
    bump), same non-USD-notional caveat as PV_USD.
  - ``PAR_RATE`` = ``SwapResult.par_rate`` (the swap's fair fixed rate, decimal, e.g.
    0.0398 for 3.98%).
  - ``CASHFLOW_USD`` (2026-09-17) = ``SwapResult.realised_cashflows`` converted the same
    way: net coupons already settled on or before `as_of`, so the P&L engine's swap LTD
    (``PV_USD + CASHFLOW_USD``, CLAUDE.md "P&L conventions") is continuous across a
    coupon payment and at maturity, when PV_USD goes to 0 and the whole result sits in
    CASHFLOW_USD.
  - Non-USD notional (2026-09-17, closes docs/open-questions.md item 53): PV, DV01 and
    cashflows are computed in the swap's currency and converted at that day's official
    SPOT (``engine.pnl.valuation.usd_per_quote``, the same conversion FX legs get). No
    SPOT on file -> ``ValueError``; an unconverted number is never written under a
    ``_USD`` name.
  - Fixings (2026-09-17): every ``index_fixings`` row for the swap's index is loaded into
    the QuantLib index before pricing, so a seasoned swap (effective date in the past)
    values. A missing past fixing still raises from QuantLib; nothing is invented.
"""
from __future__ import annotations

import datetime
import sqlite3
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from .conventions import CCY_RFR
from .curves import CurveSet, build_curve_set
from .valuation import SwapResult, price_swap

_NY_ZONE: Optional[ZoneInfo] = None


def _ny() -> ZoneInfo:
    """Lazily resolve America/New_York (see data/bloomberg/pull_marks.py::_ny for why
    this must not be a module-level constant: ZoneInfo("America/New_York") raises
    immediately on a stock Windows Python without the `tzdata` package)."""
    global _NY_ZONE
    if _NY_ZONE is None:
        _NY_ZONE = ZoneInfo("America/New_York")
    return _NY_ZONE


def snapped_at(as_of: datetime.date) -> str:
    """15:00 America/New_York (the official close, user decision 2026-09-21) on as_of, ISO with the resolved offset for that date --
    same close convention as CLAUDE.md "Mark time" / data/bloomberg/pull_marks.py."""
    return datetime.datetime(as_of.year, as_of.month, as_of.day, 15, 0, 0, tzinfo=_ny()).isoformat()


def _curve_id(ccy: str, index: str) -> str:
    return f"{ccy}-{index}-OIS"


def load_fixings(conn: sqlite3.Connection, curve_set: CurveSet) -> int:
    """Add every `index_fixings` row for the curve set's index (BBG_BDH preferred over
    MANUAL when both exist for a date) to the QuantLib index, skipping dates that are
    not business days on the index calendar (QuantLib rejects them). Returns the number
    of fixings loaded."""
    from . import qlmap

    rows = conn.execute(
        'SELECT fixing_date, value, source FROM index_fixings WHERE "index" = ? '
        "ORDER BY fixing_date, CASE source WHEN 'BBG_BDH' THEN 0 ELSE 1 END",
        (curve_set.index,),
    ).fetchall()
    seen = set()
    index = curve_set.ql_index
    cal = index.fixingCalendar()
    n = 0
    for fixing_date, value, _source in rows:
        if fixing_date in seen:
            continue
        seen.add(fixing_date)
        d = qlmap.ql_date(datetime.date.fromisoformat(fixing_date))
        if not cal.isBusinessDay(d):
            continue
        index.addFixing(d, float(value), True)
        n += 1
    return n


def _usd_per_ccy(conn: sqlite3.Connection, ccy: str, as_of: str) -> float:
    """USD per 1 unit of `ccy` at the official SPOT on `as_of` (1.0 for USD)."""
    if ccy == "USD":
        return 1.0
    from engine.pnl.valuation import usd_per_quote

    s, _pair, _src = usd_per_quote(conn, ccy, as_of)
    if s != s:
        raise ValueError(f"No official SPOT on {as_of} to convert {ccy} swap values to USD")
    return float(s)


def _read_curve_quotes(conn: sqlite3.Connection, as_of: str, ccy: str, index: str) -> List[Tuple[str, str, float]]:
    """Rows for (as_of, ccy, index) from curve_quotes, one source only. Multiple
    sources are not blended: prefer 'BBG_BDP' (the live pull's default source, see
    data/bloomberg/rates_marketdata.py::write_curve_quotes) if present, else whichever
    source sorts first alphabetically, for determinism."""
    rows = conn.execute(
        'SELECT tenor, value, source FROM curve_quotes '
        'WHERE as_of_date = ? AND ccy = ? AND "index" = ? AND quote_type = ?',
        (as_of, ccy, index, "OIS"),
    ).fetchall()
    if not rows:
        return []
    sources = sorted({r[2] for r in rows})
    source = "BBG_BDP" if "BBG_BDP" in sources else sources[0]
    picked = [(tenor, value) for tenor, value, src in rows if src == source]
    return picked


def bootstrap_and_store(
    conn: sqlite3.Connection, as_of: str, ccy: str, index: Optional[str] = None
) -> CurveSet:
    """Bootstrap the OIS curve for (ccy, index) as of `as_of` (ISO date string) from
    `curve_quotes`, and write one `curves` row per quoted tenor (`node_date` = the
    OIS helper's pillar date implied by that tenor, `discount_factor` from the
    bootstrapped curve, `par_rate` = the raw quoted rate, `source='QL_PRICER'`).
    Returns the in-memory CurveSet (see module docstring for why).
    """
    index = index or CCY_RFR.get(ccy)
    if index is None:
        raise ValueError(f"No canonical OIS index for currency {ccy!r}; supported: {sorted(CCY_RFR)}")

    quotes = _read_curve_quotes(conn, as_of, ccy, index)
    if not quotes:
        raise ValueError(f"No curve_quotes rows for ({as_of}, {ccy}, {index})")

    as_of_date = datetime.date.fromisoformat(as_of)
    curve_set = build_curve_set(quotes, as_of_date, ccy, index)

    import QuantLib as ql

    from . import qlmap
    from .conventions import CONVENTIONS

    conv = CONVENTIONS.get(ccy, index)
    cal = qlmap.calendar(conv.calendar)
    as_of_ql = qlmap.ql_date(as_of_date)
    spot_ql = cal.advance(as_of_ql, int(conv.spot_lag), ql.Days)

    curve_id = _curve_id(ccy, index)
    rows = []
    for tenor, value in quotes:
        node_ql = cal.advance(spot_ql, qlmap.period(tenor), qlmap.bdc(conv.business_day_convention))
        node_date = qlmap.py_date(node_ql).isoformat()
        df = curve_set.discount_curve.discount(node_ql)
        rows.append((curve_id, as_of, node_date, df, float(value), "QL_PRICER"))

    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO curves (curve_id, as_of_date, node_date, discount_factor, par_rate, source) "
            "VALUES (?,?,?,?,?,?)",
            rows,
        )
    return curve_set


def _read_irs_trade(conn: sqlite3.Connection, trade_id: str):
    trade = conn.execute(
        "SELECT trade_id, instrument_id, product, trade_date, quantity, price "
        "FROM trades WHERE trade_id = ?",
        (trade_id,),
    ).fetchone()
    if trade is None:
        raise ValueError(f"No trade {trade_id!r}")
    _tid, instrument_id, product, trade_date, quantity, price = trade
    if product != "IRS":
        raise ValueError(f"Trade {trade_id!r} has product {product!r}, expected 'IRS'")

    instrument = conn.execute(
        "SELECT base_ccy, quote_ccy FROM instruments WHERE instrument_id = ?", (instrument_id,)
    ).fetchone()
    if instrument is None:
        raise ValueError(f"No instrument {instrument_id!r}")
    ccy = instrument[0]

    legs = conn.execute(
        "SELECT leg_type, start_date, settle_date FROM trade_legs WHERE trade_id = ? ORDER BY leg_no",
        (trade_id,),
    ).fetchall()
    fixed_leg = next((l for l in legs if l[0] == "FIXED"), None)
    if fixed_leg is None:
        raise ValueError(f"Trade {trade_id!r} has no FIXED leg")
    effective_date = datetime.date.fromisoformat(fixed_leg[1])
    maturity_date = datetime.date.fromisoformat(fixed_leg[2])

    return instrument_id, ccy, trade_date, quantity, price, effective_date, maturity_date


def price_and_store(conn: sqlite3.Connection, as_of: str, trade_id: str,
                    curve_set: Optional[CurveSet] = None) -> SwapResult:
    """Price one IRS trade as of `as_of` and write PV_USD / DV01_USD / CASHFLOW_USD /
    PAR_RATE marks (source='QL_PRICER'). Bootstraps (and stores) the OIS curve for the
    trade's currency via `bootstrap_and_store` unless `curve_set` (already built for
    that currency and date, fixings loaded) is passed, so `curve_quotes` for (as_of,
    ccy, canonical RFR index) must already be populated.
    """
    instrument_id, ccy, _trade_date, quantity, fixed_rate, effective_date, maturity_date = _read_irs_trade(
        conn, trade_id
    )
    if curve_set is None or curve_set.ccy != ccy or curve_set.valuation_date.isoformat() != as_of:
        curve_set = bootstrap_and_store(conn, as_of, ccy)
        load_fixings(conn, curve_set)
    fx = _usd_per_ccy(conn, ccy, as_of)

    pay_fixed = quantity > 0
    notional = abs(quantity)

    result = price_swap(curve_set, effective_date, maturity_date, fixed_rate, notional, pay_fixed)

    settle_date = maturity_date.isoformat()
    snapped = snapped_at(datetime.date.fromisoformat(as_of))
    rows = [
        (as_of, instrument_id, settle_date, "PV_USD", result.npv * fx, "QL_PRICER", snapped),
        (as_of, instrument_id, settle_date, "DV01_USD", result.dv01_parallel * fx, "QL_PRICER", snapped),
        (as_of, instrument_id, settle_date, "CASHFLOW_USD", result.realised_cashflows * fx, "QL_PRICER", snapped),
    ]
    if result.par_rate is not None:
        rows.append((as_of, instrument_id, settle_date, "PAR_RATE", result.par_rate, "QL_PRICER", snapped))

    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO marks "
            "(as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at) "
            "VALUES (?,?,?,?,?,?,?)",
            rows,
        )
    return result


def price_all_and_store(conn: sqlite3.Connection, as_of: str) -> List[dict]:
    """Price every IRS trade in `trades_official` dealt on or before `as_of` that is not
    already frozen in `realised_pnl`, one curve bootstrap per currency. Never raises for
    a single trade: returns one dict per trade, `{trade_id, instrument_id, ccy, ok,
    error, interpolation, note}`, so a swap with a missing fixing or an unsupported
    currency reports its own reason while the rest of the book still prices. A currency
    with no `curve_quotes` fails every swap in that currency with the same reason.
    `interpolation` is the interpolation the currency's curve was built with
    ("LogLinear", flat forwards, the default since 2026-09-22, see curves.py) and
    `note` its `CurveSet.bootstrap_note` (always "" now that a non-converging curve
    raises instead of falling back), both "" when no curve could be built; the pull's
    status shows them."""
    trades = conn.execute(
        "SELECT t.trade_id, t.instrument_id, i.base_ccy FROM trades_official t "
        "JOIN instruments i USING (instrument_id) "
        "WHERE t.product = 'IRS' AND t.trade_date <= ? "
        "AND t.trade_id NOT IN (SELECT trade_id FROM realised_pnl) ORDER BY i.base_ccy, t.trade_id",
        (as_of,),
    ).fetchall()
    out: List[dict] = []
    curves: dict = {}
    for trade_id, instrument_id, ccy in trades:
        entry = {"trade_id": trade_id, "instrument_id": instrument_id, "ccy": ccy, "ok": False, "error": "",
                 "interpolation": "", "note": ""}
        try:
            if ccy not in curves:
                cs = bootstrap_and_store(conn, as_of, ccy)
                load_fixings(conn, cs)
                curves[ccy] = cs
            entry["interpolation"] = curves[ccy].interpolation
            entry["note"] = curves[ccy].bootstrap_note
            price_and_store(conn, as_of, trade_id, curve_set=curves[ccy])
            entry["ok"] = True
        except Exception as exc:  # noqa: BLE001 - reported per trade, never swallowed silently
            entry["error"] = f"{type(exc).__name__}: {exc}"
        out.append(entry)
    return out


# --------------------------------------------------------------------------- direction flip
# Swap marks whose value is exactly minus itself for the opposite direction. PAR_RATE is
# direction-free and is never touched.
DIRECTIONAL_MARK_TYPES = ("PV_USD", "DV01_USD", "CASHFLOW_USD")
# This package's own source (CLAUDE.md "Official marks": QL_PRICER is official for these
# mark types), the rows reversed in place; rows from any other source are deleted on a flip.
PRICER_SOURCE = "QL_PRICER"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def reverse_direction_marks(conn: sqlite3.Connection, trade_id: str, instrument_id: str) -> None:
    """Turn one flipped swap's priced history round: the pricer's own marks, reversed by
    the pricer. Called by ``data/ingest/irs_direction.py`` (``set_direction``,
    ``apply_overrides``, ``reverse_flipped``) once a swap's ``trades.quantity`` and legs
    have actually changed sign, inside the caller's transaction: this function commits
    nothing and rolls back nothing, so a failure here undoes the flip with it.

    Why reversal is exact and not an approximation: a vanilla single-currency OIS
    receiver is the same two legs as the payer with every cashflow's sign swapped,
    priced on the same curve, so its NPV, its bump-and-reprice DV01 (stored signed:
    bumped NPV minus NPV) and its settled cashflows are each exactly minus the payer's,
    and the fair rate does not depend on which side you are on. The USD conversion is
    one positive factor on both sides, so it holds for non-USD swaps too. Proved against
    this package itself in tests/test_rates_pricing.py and tests/test_ingest.py:
    forward-starting, seasoned with a coupon already paid, and expired swaps all come
    out bit-for-bit negative, and the reversed marks equal what ``price_and_store``
    writes for the flipped trade. Why reverse rather than delete: swaps are priced for
    today by the live pull and the past days only by ``recalc_on_file``, so deleted
    history would leave the swap's Daily, 5d, MTD and YTD blank until the next rerun.

    For `instrument_id`, on every as_of_date:
      - PRICER_SOURCE rows of PV_USD / DV01_USD / CASHFLOW_USD: value * -1 in place
        (0 stays 0.0, never -0.0);
      - rows of those three types from any other source (BBG_BDH SWPM reconciliation,
        MANUAL: nothing records which direction they were entered for), and any
        PRICER_SOURCE row whose stored value is not a number (nothing to reverse):
        deleted;
      - PAR_RATE, and every other mark type: untouched.
    Then the trade's `realised_pnl` row is deleted so the ledger re-realises it from the
    reversed marks. If another trade shares the instrument the marks cannot be attributed
    to this one, so all three types are deleted instead of reversed. A database without
    a `marks` or `realised_pnl` table is left alone."""
    if _table_exists(conn, "marks"):
        types = ",".join("?" for _ in DIRECTIONAL_MARK_TYPES)
        shared = conn.execute("SELECT COUNT(*) FROM trades WHERE instrument_id = ?", (instrument_id,)).fetchone()[0] > 1
        if shared:
            conn.execute(f"DELETE FROM marks WHERE instrument_id = ? AND mark_type IN ({types})",
                         (instrument_id, *DIRECTIONAL_MARK_TYPES))
        else:
            conn.execute(
                f"DELETE FROM marks WHERE instrument_id = ? AND mark_type IN ({types}) "
                "AND (source != ? OR typeof(value) NOT IN ('real', 'integer'))",
                (instrument_id, *DIRECTIONAL_MARK_TYPES, PRICER_SOURCE))
            conn.execute(
                f"UPDATE marks SET value = CASE WHEN value = 0 THEN 0.0 ELSE -value END "
                f"WHERE instrument_id = ? AND mark_type IN ({types}) AND source = ?",
                (instrument_id, *DIRECTIONAL_MARK_TYPES, PRICER_SOURCE))
    if _table_exists(conn, "realised_pnl"):
        conn.execute("DELETE FROM realised_pnl WHERE trade_id = ?", (trade_id,))


_OIS_QUOTE_DAYS_SQL = (
    "SELECT DISTINCT as_of_date FROM curve_quotes WHERE quote_type = 'OIS' "
    "AND as_of_date >= :since AND as_of_date <= :as_of ORDER BY as_of_date"
)


def recalc_on_file(conn: sqlite3.Connection, as_of: str, since: Optional[str] = None) -> dict:
    """Re-price every IRS from the data ON FILE, asking Bloomberg nothing (user,
    2026-09-22: "yes switch to flat forwards and rerun the past days"). The past days'
    ``curves`` rows and ``PV_USD`` / ``DV01_USD`` / ``CASHFLOW_USD`` / ``PAR_RATE``
    marks were written off the log-cubic curve (or not at all, on the days it did not
    converge); the OIS quotes of every day are on file in ``curve_quotes`` (the pull's
    own or the imported snapshot's), so the marks are rebuilt from them under the
    interpolation ``curves.py`` now defaults to. Also the pull button's no-Bloomberg
    branch for swaps, like ``engine/options/store.py::recalc_on_file`` for options.

    Runs ``price_all_and_store(conn, day)`` for every ``as_of_date`` in ``curve_quotes``
    (quote_type 'OIS') from `since` (default: the earliest such date on file) to `as_of`
    inclusive, ascending: each day rebuilds and stores the curve per currency from that
    day's own quotes and re-prices every swap dealt on or before it and not frozen in
    ``realised_pnl``, INSERT OR REPLACE under the same keys, so the old rows are
    replaced and the run is idempotent. A day priced at another day's data never
    happens; a currency whose quotes do not build, or a swap missing a fixing, is
    reported under that day's ``failed`` with its reason, as ``price_all_and_store``
    reports it, and the rest of the day still prices. ``snapped_at`` is the day's
    official close (``snapped_at(day)``), as ``price_and_store`` always writes.

    Never raises. Returns ``{"as_of", "since", "days": [{"day", "priced", "failed":
    [{"trade_id", "error"}, ...]}, ...], "priced": <total>, "failed": <total count>}``,
    plus ``"error"`` (repr) if something outside the per-day calls raised, with the
    counts so far."""
    out: dict = {"as_of": as_of, "since": since, "days": [], "priced": 0, "failed": 0}
    try:
        datetime.date.fromisoformat(as_of)
        if since is None:
            row = conn.execute("SELECT MIN(as_of_date) FROM curve_quotes WHERE quote_type = 'OIS'").fetchone()
            since = row[0] if row and row[0] else as_of
            out["since"] = since
        else:
            datetime.date.fromisoformat(since)
        days = [r[0] for r in conn.execute(_OIS_QUOTE_DAYS_SQL, {"since": since, "as_of": as_of}).fetchall()]
        for day in days:
            outcomes = price_all_and_store(conn, day)
            entry = {
                "day": day,
                "priced": sum(1 for o in outcomes if o["ok"]),
                "failed": [{"trade_id": o["trade_id"], "error": o["error"]} for o in outcomes if not o["ok"]],
            }
            out["days"].append(entry)
            out["priced"] += entry["priced"]
            out["failed"] += len(entry["failed"])
    except Exception as exc:  # noqa: BLE001 - reported, never raised: the caller is a button / a subcommand
        out["error"] = f"{exc!r}"
    return out
