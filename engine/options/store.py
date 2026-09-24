"""Glue between engine/options' pricers and this app's SQLite schema: reads
FX_OPTION trades from ``trades_official`` / ``instruments`` /
``instrument_options``, writes ``PREMIUM`` / ``DELTA`` / ``GAMMA`` / ``THETA``
/ ``VEGA`` / ``RHO`` marks (``source='QL_OPTIONS_PRICER'``) -- the same shape
of glue ``engine/rates/store.py`` provides for the rates pricer.

Three entry points (the first two mirror engine/rates/store.py):
  - ``price_and_store(conn, as_of, trade_id)``: price one FX_OPTION trade,
    write its marks, return a ``PricingOutcome``.
  - ``price_all_and_store(conn, as_of)``: same, for every FX_OPTION trade in
    ``trades_official`` as of that date -- the LIVE pull's step
    (``data/bloomberg/live.py::_options_step``).
  - ``price_close(conn, day)`` (2026-09-22): a PAST CLOSE, for the backfill to call for
    each day it works, strictly from that day's own inputs. See "Past closes" below.
  - ``recalc_on_file(conn, as_of)`` (2026-09-22): every day's option marks rebuilt from the
    data ON FILE, no Bloomberg -- ``price_close`` for each past day with inputs, then
    ``price_all_and_store`` for ``as_of``. For the pull button on a PC without a terminal
    (user: "pull bbg now should recalc options too, using log data if no bbg access, or
    pull new data for new calculation").

**Options on commodity futures (2026-09-24, commodity conversion Phase 5).** All three bulk
passes also write the Greeks of every CMDTY_OPTION open on the date priced
(``price_listed_options``, from ``equity_commodity.price_listed_commodity_option``): each
instrument once, a closed-out group skipped and reported as for FX, no expiry-day payoff (the
P&L is Bloomberg's own price, frozen by the ledger). Their outcomes carry
``product='CMDTY_OPTION'``; the dicts count them apart under ``"futures_options_priced"``. See
"options on commodity futures" below.

**Past closes and the stamp on a mark (2026-09-22).** The user: "options daily pnl 0,
that cannot be right, everything is moving", then "even on the bbg machine, it seems the
options are not priced live. I expect every time I pull bloomberg now, the options are
repriced with the latest data, and that the latest data is also logged / overwriting
previous marks". On the Bloomberg PC the pricer marks existed for the latest day only:
the live pull writes today's, nothing wrote a past day's, and ``engine/pnl/valuation.py``'s
near-marks rule then carried today's PREMIUM back to the previous close, so LTD(t-1) =
LTD(t) and Daily = 0 for every option. Two things here, both landed 2026-09-22:
  * ``price_close(conn, day)`` prices every FX_OPTION trade dealt on or before ``day``
    and not expired before it, from ``day``'s own official SPOT, forward curve, OIS
    quotes (or the covered-interest-parity fallback off that day's forwards) and vol
    smile -- every resolver in ``inputs.py`` / ``rates.py`` reads its as_of's rows
    exactly, so a day with an input missing skips the trade with a reason naming the day
    and the input, never a later day's vol, curve or spot. Marks are dated ``day``,
    written INSERT OR REPLACE (a re-run overwrites), stamped that day's 15:00 New York
    close with the offset resolved for the date. Expiry on ``day`` writes the payoff at
    that day's official SPOT (the "Expiry day" rule below) unless the ledger has already
    frozen the trade from an expiry-dated mark, in which case mark and frozen row are
    left alone (the W-2 rule); an option expired before ``day`` is left to the catch-up
    in ``price_all_and_store``; a trade dealt after ``day`` is not in that day's book
    and is neither priced nor listed. Never raises: an exception becomes ``"error"`` in
    the returned dict, one trade's pricer error a skip. Returns
    ``{"day", "priced", "skipped": [{"trade_id", "reason"}]}``.
  * ``snapped_at``: a mark priced by a LIVE run carries the actual pricing time in New
    York (``live_stamp``: now, America/New_York, seconds) -- until 2026-09-22 every mark
    was stamped a flat 15:00 of its as_of, so marks priced at 23:08 said 15:00 and the
    user could not see that a pull had re-priced them. A mark that stands for a past
    close (``price_close``, and the expiry-dated catch-up marks written later) carries
    that day's 15:00 New York close stamp (``close_stamp``), like every other close the
    backfill writes. ``price_and_store`` / ``price_all_and_store`` accept an explicit
    ``snapped`` for a caller that knows better; the live pull passes none.
  * Closed-out options (same day, later; user: "we dont need to price all options, as
    some of them might be closed out already ... we just present the buy and sell price
    as pnl"): a trade of an option bought and sold back, quantities netting to zero as
    of the date priced, by the P&L's own grouping rule (``engine/pnl/valuation.py``,
    CLAUDE.md "A closed-out option is not live"), is left out of every bulk pass --
    ``price_all_and_store`` (catch-up included), ``price_close``, ``recalc_on_file`` --
    and reported under its own head: ``PricingOutcome.closed_out = True`` with a reason,
    the dicts' ``"closed_out"`` list / count, never among ``skipped``. See "Closed-out
    options" at ``closed_out_options`` below.

**Conventions restated here (see CLAUDE.md, pricer.py, inputs.py for the
full detail):**
  - ``trades_official`` is read, never raw ``trades`` -- avoids double-
    counting a trade the BNP snapshot also carries under a different
    trade_id scheme (schema.py's view docstring).
  - ``trades.quantity > 0`` = long base currency (CLAUDE.md sign
    convention). This module does not apply that sign to PREMIUM/DELTA/etc:
    every mark written here is PER 1 UNIT of ``trades.quantity`` (pricer.py's
    own convention, matching the marks table's DELTA comment), so the sign
    is applied downstream by whoever reads the mark and multiplies by
    ``trades.quantity`` (engine/ladder's delta query does exactly this).
  - A trade that cannot be priced (missing strike, missing barrier_level,
    missing market inputs, unsupported payoff, or an expiry date that is
    already over -- see "Expiry day" below for the expiry date itself and for
    the catch-up) writes NO marks and comes back as a ``PricingOutcome``
    with ``priced=False`` and a ``reason`` -- never a fabricated number.
  - ``instrument_options.strike``/``barrier_level`` use the schema's own
    "0 = not known" sentinel (schema.py comment) -- real blotter rows with
    no strike embedded in their free-text Description land here as strike
    0.0, and this module treats that exactly as "not known", not as a
    genuine zero strike.
  - **Unit of every mark written here (2026-09-18 units audit).** All seven are PER 1
    UNIT OF ``trades.quantity`` and for a LONG position; multiply by the signed
    ``trades.quantity`` to get the trade's own figure. The pair is quoted QUOTE ccy per
    1 BASE ccy (EURSEK = SEK per EUR, USDJPY = JPY per USD); S below is that spot.

      PREMIUM   dimensionless fraction, paid in the BASE currency: base-ccy value /
                base notional for VANILLA, AMERICAN, ASIAN, BARRIER_KI/KO; base-ccy
                value / base-ccy PAYOUT for DIGITAL, ONE_TOUCH, NO_TOUCH (where
                ``trades.quantity`` IS the payout, see CASH_PAYOUT_CCY below), so it
                lies in [0, base-ccy discount factor] for those three. Same unit as
                the blotter fill ``trades.price`` for every pair orientation (EURSEK
                cross, EURUSD, USDJPY): not pips, not % , not quote currency.
                Starting cost (base ccy) = quantity x trades.price; market value (base
                ccy) = quantity x PREMIUM; P&L_USD = quantity x (PREMIUM - fill) x
                (USD per 1 BASE ccy at spot) -- CLAUDE.md's FX option line.
      DELTA     BASE-ccy units per 1 unit of quantity: d(PREMIUM x S)/dS, the raw spot
                delta (not premium-adjusted). quantity x DELTA = base-ccy position,
                -quantity x DELTA x S = quote-ccy position (CLAUDE.md's delta query).
                A digital near its strike is several times its payout (|DELTA| > 1).
      DELTA_PA  DELTA - PREMIUM, BASE-ccy units; only for pairs whose market
                convention is premium-adjusted. The ladder never reads it.
      GAMMA     change in DELTA per move of 1.0 in S AS QUOTED (1 SEK per EUR, 1 JPY
                per USD, 1.00 USD per EUR): NOT per 1 %, NOT per pip. Its size depends
                on the pair's price scale (EURUSD ~ tens, USDJPY ~ hundredths), so it
                must be rescaled before it is summed across pairs:
                GAMMA x S / 100 = change in DELTA per 1 % spot move.
      VEGA      QUOTE ccy per 1 vol POINT (vol 10 % -> 11 %).
      THETA     QUOTE ccy per 1 CALENDAR day of time decay (negative = decay costs
                the holder).
      RHO       QUOTE ccy per 1 PERCENTAGE POINT (100 bp) rise in the QUOTE
                currency's (domestic) rate. Foreign-rate rho is not stored.

    To USD, for a headline that sums across pairs (b2u / q2u = USD per 1 BASE / QUOTE
    ccy at the official SPOT; b2u = S x q2u):
      market value  quantity x PREMIUM x b2u
      delta         quantity x DELTA x b2u                 (USD delta notional)
      gamma         quantity x GAMMA x S / 100 x b2u       (USD delta change per 1 % move)
      vega / theta / rho   quantity x {VEGA, THETA, RHO} x q2u
    Converting DELTA or GAMMA with q2u alone (as THETA/VEGA/RHO are) gives "USD P&L
    per 1.0 move of S", which is not a delta and cannot be added across pairs.
    ``engine/options/portfolio.py`` applies exactly the conversions above.
  - **Expiry day (2026-09-18, approved by the user).** The ledger realises an expired
    option at the last official PREMIUM on or before its expiry
    (``engine/pnl/ledger.py::realise_settled``, which only freezes once the expiry day is
    OVER: ``settle_date < as_of``). Until this landed the pricer skipped ``expiry <=
    as_of``, so that PREMIUM was the T-1 MODEL premium (or an older one): a day of time
    value frozen into realised P&L, and the last day's spot move lost. Now:
      * ``as_of == expiry``: PREMIUM = the PAYOFF at the pair's official SPOT for that
        date (``marks_official``; ``pricer.price_fx_at_expiry``), in the same unit as
        every other PREMIUM: vanilla / american call max(S-K,0)/S, put max(K-S,0)/S;
        digital 1.0 in the money else 0.0 (fraction of the BASE-ccy payout). "In the
        money" is strict, the vendored pricers' and QuantLib's own convention: at S == K
        every payoff is 0. No vol, no rate, no model is involved -- and none is ever used
        as a fallback on expiry day: no SPOT -> skipped "no SPOT mark", nothing written,
        never a 0 standing in for "unknown". DELTA = the payoff's own spot delta in the
        units above (+1 call in the money, -1 put in the money, 0 otherwise: until the
        cut the holder is long / short one BASE unit against K of QUOTE, and out of the
        money nothing is left; a BASE-payout digital in the money is one BASE unit about
        to be received, so its DELTA is +1 for call and put alike and its DELTA_PA 0);
        GAMMA = THETA = VEGA = RHO = 0.0 because no time value is left and nothing
        depends on vol, time or rates any more; DELTA_PA = DELTA - PREMIUM as always,
        for premium-adjusted pairs only. Every pull of the day rewrites the row, so its
        final value is that day's last official spot.
      * ASIAN / BARRIER_KI / BARRIER_KO / ONE_TOUCH / NO_TOUCH depend on the path spot
        took, which the closing spot cannot tell: skipped with a reason saying so (none
        is in the book).
      * ``as_of > expiry``: skipped as before ("expiry ... is not after as_of ...").
      * Catch-up, in ``price_all_and_store`` only (every live pull), for an option that
        expired before ``as_of`` and is NOT YET FROZEN from an expiry-dated mark: the
        expiry-dated intrinsic marks are made to agree with the official SPOT ON FILE
        NOW for the expiry date. No official PREMIUM dated the expiry date (the app did
        not run that day): written, DATED THE EXPIRY DATE, from that date's official
        SPOT (the historical backfill writes closing SPOT rows); no SPOT for that date:
        nothing is written, and the skip reason says so. One on file whose PREMIUM
        differs from the payoff recomputed from the SPOT now on file (the last live
        pull of expiry day saw 151.90, the close that later replaced it says 152.30):
        rewritten from the SPOT on file; identical: left alone. In the same transaction
        the instrument's ``realised_pnl`` rows frozen from a premium dated before the
        expiry date are deleted, so the ledger's next pass -- which
        ``data/bloomberg/live.py::pull_once`` runs right after the options step (marks
        written, options step incl. this catch-up, then ``realise_settled``) -- freezes
        them at the intrinsic (same reasoning as ``set_option_terms``: a figure frozen
        from the wrong premium is not a realised P&L, and the ledger never revisits a
        trade that has a row). Once a row IS frozen from an expiry-dated mark, mark and
        row are both left alone for good.
    KNOWN LIMITS, documented, deliberately not solved here:
      * Cut time. The true payoff is fixed at the option's cut (the blotter's 'Cut Time'
        / 'Cut Location' columns: 10:00 New York on six of the book's eight options,
        15:00 Tokyo on one, and one row whose location reads 'NONE'), which the app does
        not store. The payoff frozen is the one AT THE OFFICIAL SPOT ON FILE FOR THE
        EXPIRY DATE AT THE MOMENT OF THE FIRST FREEZE -- the last live pull stamped with
        the expiry date, or that date's 15:00 New York close where a backfill / import
        has since replaced it or the app did not run that day -- still not the spot at
        the cut, hours earlier. The app's day is the NEW YORK date
        (``data/bloomberg/live.py::book_today``), so a pull made in Asia on the following
        morning, before midday Hong Kong time, still rewrites the expiry date's SPOT and
        mark, with a spot observed after the New York close. A SPOT for the expiry date
        that changes AFTER the first freeze changes nothing.
      * Delivery double count. If an exercised option is delivered as a spot trade booked
        AT THE STRIKE, the book counts (S - K) twice: once in the option frozen at its
        intrinsic, once in the delivery trade's own P&L against the market. Nothing here
        adjusts for that.
  - Vol provenance (Phase 5b, 2026-09-17): ``resolve_market_inputs``
    (inputs.py) resolves each priced trade's vol via SMILE / ATM_INTERP /
    MANUAL, in that priority. ``PricingOutcome.vol_source_kind`` /
    ``.vol_detail`` record which one actually fed the marks written for
    that trade, so a reader never has to re-derive it. One `surface_cache`
    dict is shared across every trade in a single ``price_all_and_store``
    run so trades sharing a pair don't each rebuild that pair's
    FXDeltaVolSurface (see inputs.py::_cached_surface).
  - Digitals are priced ON THE SMILE (user decision 2026-09-21: "yes, price off the
    smile"). When the vol came off a smile (``vol_source_kind == 'SMILE'``) a DIGITAL is
    valued as a tight call / put spread, each leg at its own smile vol
    (``pricer.price_fx_digital_on_smile``), so the slope of the smile is in the price; one
    vol read at the strike overstated the book's USDJPY 152 digital puts by 2 to 5 points
    of payout on a typical smile. ``vol_detail`` then ends with ``SMILE_DIGITAL_DETAIL``.
    With an ATM-interpolated or a manual vol no slope is known and the single-vol closed
    form is used, as before. Touches and barriers are unchanged.
  - Rate provenance (Phase 7.1, 2026-09-17; wired onto PricingOutcome Phase
    7.2, 2026-09-18): ``PricingOutcome.domestic_rate_source_kind`` /
    ``.domestic_rate_detail`` and ``.foreign_rate_source_kind`` /
    ``.foreign_rate_detail`` mirror the vol fields above, one pair per
    currency leg -- ``OIS_CURVE`` / ``MANUAL_EXPIRY`` / ``MANUAL_FLAT`` /
    ``IMPLIED_FORWARD`` (``engine/options/rates.py::RateInput.source_kind``).
    An ``IMPLIED_FORWARD`` detail reads e.g. "implied from EURSEK forward
    2026-11-25 and EUR ESTR curve" -- this is the string a diagnostics
    screen should surface for a covered-interest-parity-implied rate.
"""
from __future__ import annotations

import dataclasses
import datetime
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional

from . import pricer
from .inputs import resolve_market_inputs
from engine.rates.store import snapped_at

# Payoffs this phase can dispatch. AMERICAN/ASIAN/BARRIER_KI/BARRIER_KO/
# ONE_TOUCH/NO_TOUCH landed Phase 4; anything else (or an unrecognized
# string) is a structured skip, not an error, so a bad/blank payoff value
# never silently falls through to VANILLA.
_STRIKE_PAYOFFS = {"VANILLA", "DIGITAL", "AMERICAN", "ASIAN", "BARRIER_KI", "BARRIER_KO"}
_BARRIER_PAYOFFS = {"BARRIER_KI", "BARRIER_KO", "ONE_TOUCH", "NO_TOUCH"}

_MARK_FIELDS = ("PREMIUM", "DELTA", "GAMMA", "THETA", "VEGA", "RHO")

# What this package writes to `marks`, and therefore what a terms change invalidates
# (`set_option_terms`): the six `_MARK_FIELDS` plus DELTA_PA, all under one source.
PRICER_SOURCE = "QL_OPTIONS_PRICER"
PRICER_MARK_TYPES = _MARK_FIELDS + ("DELTA_PA",)

# Payout currency of the cash payoffs (DIGITAL / ONE_TOUCH / NO_TOUCH). 'BASE': the
# option pays `trades.quantity` units of the pair's BASE currency, and both the fill and
# the PREMIUM mark are a fraction of that payout, paid in base ccy. That is how the book's
# three digitals are booked (data/raw/new_sample_trades.csv: EURSEK112526C 1,000,000 @
# 0.121 = EUR 121,000; USDJPY111926P 2,000,000 @ 0.124 = USD 248,000 and 1,000,000 @
# 0.1425 = USD 142,500 -- the invoice currency is the base currency, and a premium of
# that size rules out a payout of the same number of SEK or JPY). 'QUOTE' would mean the
# payout is `trades.quantity` QUOTE-ccy units valued back into base ccy at spot; no ticket
# on file is booked that way. One constant, not a per-instrument column: the blotter
# carries no payout-currency field, and `instrument_options` is data-ingest's table.
# CONFIRMED BY THE USER 2026-09-18: USD on the USDJPY digitals, EUR on the EURSEK one,
# i.e. BASE (no longer an inference) -- see pricer.py's "Cash payoffs" section. The EURSEK
# figures come back to dollars by the book's existing rule: value and P&L in base ccy x
# USD per 1 base unit at that day's SPOT (engine/pnl/valuation.py), nothing changes here.
CASH_PAYOUT_CCY = pricer.PAYOUT_BASE

# Skip reasons for terms the blotter export does not carry. Every skip `reason` names the
# one thing that is missing, in words a user can act on; the market-input ones come from
# inputs.py / rates.py unchanged ("no SPOT mark", "no vol", "no curve/rate <CCY>" --
# data/bloomberg/live.py matches "no vol" exactly to append the failing Bloomberg ticker).
NO_STRIKE_REASON = "no strike on file: enter the strike (and the payoff, if it is a digital) under Option terms"
NO_BARRIER_REASON = "no barrier / touch level on file: enter it under Option terms"


# --------------------------------------------------------------------------- snapped_at stamps (2026-09-22)
# Module docstring, "Past closes and the stamp on a mark".

def _now_ny() -> datetime.datetime:
    """The clock, in America/New_York: the moment a live pricing run happens. Its own
    function so a test can pin it (two runs in one second must still order)."""
    from zoneinfo import ZoneInfo

    return datetime.datetime.now(ZoneInfo("America/New_York"))


def live_stamp() -> str:
    """`snapped_at` of a mark priced NOW: the actual pricing time with the New York offset,
    to the second, as the pull's own SPOT rows carry theirs. A pull at 23:08 New York
    stamps 23:08, not the day's 15:00 close."""
    return _now_ny().isoformat(timespec="seconds")


def close_stamp(day: str) -> str:
    """`snapped_at` of a mark that stands for `day`'s official close: 15:00 America/New_York
    on that date, offset resolved for the date (`engine.rates.store.snapped_at`, the stamp
    the rates pricer uses for the same purpose)."""
    return snapped_at(datetime.date.fromisoformat(day))


@dataclass
class PricingOutcome:
    trade_id: str
    instrument_id: str
    package_id: str
    quantity: float
    priced: bool
    reason: str = ""
    result: Optional[pricer.OptionPriceResult] = None
    # Base-six-Greek dict {'price', 'delta', 'gamma', 'theta', 'vega', 'rho'}
    # in the vendored pricer's own pre-conversion units (quote ccy for
    # 'price'), kept so engine/options/structures.py can combine legs
    # without re-pricing them.
    raw: Optional[Dict[str, float]] = None
    # Provenance of the vol that fed this outcome's marks -- SMILE |
    # ATM_INTERP | MANUAL (inputs.py::VolInput.source_kind), or None for a
    # skipped trade (no vol was resolved at all). See inputs.py's "Vol"
    # docstring section for the full priority. `vol_detail` is the paired
    # human-readable detail string, "" when vol_source_kind is None.
    vol_source_kind: Optional[str] = None
    vol_detail: str = ""
    # Rate provenance (Phase 7.2, 2026-09-18) -- see module docstring's
    # "Rate provenance" bullet. None/"" for a skipped trade (inputs never
    # resolved), same convention as the vol fields above.
    domestic_rate_source_kind: Optional[str] = None
    domestic_rate_detail: str = ""
    foreign_rate_source_kind: Optional[str] = None
    foreign_rate_detail: str = ""
    # How the marks were made and under which date they were written (2026-09-18).
    # 'MODEL' = the vendored pricer, dated the run's as_of; 'INTRINSIC' = the payoff at
    # the official SPOT of the expiry date (module docstring, "Expiry day"), dated the
    # expiry date -- which is BEFORE the run's as_of for a catch-up mark. '' for a skip.
    mark_basis: str = ""
    mark_date: str = ""
    # True for a trade of an option closed out as of the date priced (bought and sold back,
    # quantities netting to zero; "Closed-out options" below): not priced, no mark written,
    # `reason` says so. A consumer counting skips should list these under their own head
    # ("N closed-out options not priced"), never among the trades that could not be priced.
    closed_out: bool = False
    # Which product the outcome is for (2026-09-24, commodity conversion Phase 5): 'FX_OPTION'
    # or 'CMDTY_OPTION' (an option on a commodity future, "Options on commodity futures"
    # below), so a status line can count the options on futures apart. `implied_vol` is the
    # vol Bloomberg's price of a CMDTY_OPTION implies (None for FX and for a skip).
    product: str = "FX_OPTION"
    implied_vol: Optional[float] = None


# --------------------------------------------------------------------------- closed-out options (2026-09-22)
# User, 2026-09-22: "we dont need to price all options, as some of them might be closed out
# already. If they are exactly the same, same strike / underlyer / expiry / type and closed
# out, we just present the buy and sell price as pnl. We dont need to price them
# individually." The P&L already values every trade of such a group at the closing fill
# (CLAUDE.md, "A closed-out option is not live"; engine/pnl/valuation.py::closed_out_from_rows:
# same pair, call / put, payoff, strike, barrier, averaging start, expiry, quantities netting
# to zero as of the date, strike 0 never matched, a part sell-back still live) and the ledger
# freezes them as CLOSE_OUT after expiry, so no mark of theirs is ever read. The bulk passes
# (`price_all_and_store`, `price_close`, hence `recalc_on_file`) leave them out, catch-up
# included; their old marks on file are left alone. The grouping rule is the P&L's own,
# imported, never a second copy. `price_and_store` (one trade, asked for by name: the Options
# grid after a terms edit, `structures.py`) still prices whatever it is given.


def closed_out_options(conn: sqlite3.Connection, as_of: str) -> dict:
    """trade_id -> engine.pnl.valuation.CloseOut for every FX_OPTION trade of a group closed
    out as of `as_of` (trades dealt on or before it), by the P&L's own rule. The import is
    lazy, as engine/rates/store.py's of the same module: engine.pnl does not import
    engine.options today (no cycle), and this keeps engine.options importable without
    pandas / engine.pnl and leaves that direction open."""
    from engine.pnl.valuation import closed_out_options as _closed_out

    return _closed_out(conn, as_of)


def closed_out_listed_options(conn: sqlite3.Connection, as_of: str) -> dict:
    """trade_id -> CloseOut for every CMDTY_OPTION trade of a group closed out as of `as_of`,
    by the same grouping rule (`engine.pnl.valuation.closed_out_from_rows`, imported, never a
    second copy): same root, currency, expiry, call / put, payoff and strike, quantities
    netting to zero over the trades dealt on or before `as_of`, strike 0 never matched, a part
    sell-back still live. The P&L's own `closed_out_options` reads FX_OPTION rows only, so the
    rows are selected here in the shape it feeds the rule (`_opt_sql`): the grouping is on the
    terms, so a buy and its sell-back on one canonical id ('CLZ26C 80 Comdty') group exactly as
    two FX ids do. The expiry is the NOTIONAL leg's settle date, else the instrument's."""
    import pandas as pd

    from engine.pnl.valuation import closed_out_from_rows

    opt = pd.read_sql_query(_LISTED_TERMS_SQL, conn, params={"as_of": as_of, "product": LISTED_PRODUCT})
    return closed_out_from_rows(opt)


def _closed_out_skip(row: dict, closed) -> PricingOutcome:
    others = [t for t in closed.trade_ids if t != row["trade_id"]]
    outcome = _skip(row, f"closed out {closed.date}: bought and sold back with {', '.join(others) or 'itself'}; "
                         f"the P&L is the closing fill {closed.price:.6g} against the fill and no mark is needed "
                         "(engine/pnl/valuation.py); not priced")
    outcome.closed_out = True
    return outcome


def _read_option_trade(conn: sqlite3.Connection, trade_id: str) -> Optional[dict]:
    row = conn.execute(
        """
        SELECT t.trade_id, t.instrument_id, t.product, t.package_id, t.quantity, t.trade_date,
               i.base_ccy, i.quote_ccy, i.expiry_date,
               o.strike, o.option_type, o.barrier_level, o.avg_start_date, o.payoff
        FROM trades_official t
        JOIN instruments i ON i.instrument_id = t.instrument_id
        LEFT JOIN instrument_options o ON o.instrument_id = t.instrument_id
        WHERE t.trade_id = ?
        """,
        (trade_id,),
    ).fetchone()
    if row is None:
        return None
    (trade_id, instrument_id, product, package_id, quantity, trade_date,
     base_ccy, quote_ccy, expiry_date,
     strike, option_type, barrier_level, avg_start_date, payoff) = row
    return dict(
        trade_id=trade_id, instrument_id=instrument_id, product=product, package_id=package_id,
        quantity=quantity, trade_date=trade_date, base_ccy=base_ccy, quote_ccy=quote_ccy, expiry_date=expiry_date,
        strike=strike, option_type=option_type, barrier_level=barrier_level,
        avg_start_date=avg_start_date, payoff=payoff,
    )


def _skip(row: dict, reason: str) -> PricingOutcome:
    return PricingOutcome(
        trade_id=row["trade_id"], instrument_id=row["instrument_id"], package_id=row["package_id"],
        quantity=row["quantity"], priced=False, reason=reason,
    )


# Time to expiry to the hour (user decision 2026-09-21: "yes" to "fix time-to-expiry, so it
# uses actual hours to the 10am New York cut"). The vendored engine counts whole calendar
# days (QuantLib dates), so a pull at 15:00 New York for an option cut at 10:00 tomorrow was
# priced with 1.00 day to go when 0.79 remain -- about 10 % too much time value on the
# book's one- and two-day options. An option's value depends on time through vol x sqrt(T)
# (rates over part of a day are nothing), so the whole-day engine is handed
# vol x sqrt(T_hours / T_days): the same total variance as the true time to the cut. The
# factor is 0.999 on a two-month option. Vega is scaled back to the quoted vol.
CUT_HOUR_NY = 10


def cut_time_factor(conn: sqlite3.Connection, as_of: str, pair: str, expiry: datetime.date) -> float:
    """sqrt(T_hours / T_days): true time from the pricing moment -- the pair's official SPOT
    snap on `as_of` (15:00 New York when it carries no readable time) -- to 10:00 New York
    on the expiry date, over the engine's whole calendar days. 1.0 when it cannot be worked out."""
    import math
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    as_of_date = datetime.date.fromisoformat(as_of)
    days = (expiry - as_of_date).days
    if days <= 0:
        return 1.0
    moment = datetime.datetime(as_of_date.year, as_of_date.month, as_of_date.day, 15, 0, tzinfo=ny)
    row = conn.execute("SELECT snapped_at FROM marks_official WHERE as_of_date = ? AND instrument_id = ? "
                       "AND mark_type = 'SPOT'", (as_of, pair)).fetchone()
    try:
        snapped = datetime.datetime.fromisoformat(row[0])
        if snapped.tzinfo is not None:
            moment = snapped.astimezone(ny)
    except (TypeError, ValueError, IndexError):
        pass
    cut = datetime.datetime(expiry.year, expiry.month, expiry.day, CUT_HOUR_NY, 0, tzinfo=ny)
    true_days = (cut - moment).total_seconds() / 86400.0
    if true_days <= 0:
        return 1.0
    return math.sqrt(true_days / days)


SMILE_DIGITAL_DETAIL = "digital valued as a tight call / put spread on the smile"


def _smile_vol_at(inputs):
    """The smile's vol-at-strike function when the vol came off a smile, else None
    (`inputs.VolInput.vol_at`)."""
    return getattr(inputs.vol_source, "vol_at", None)


def _dispatch(row: dict, as_of_date: datetime.date, expiry: datetime.date, inputs, pair: str,
              vol_factor: float = 1.0) -> pricer.OptionPriceResult:
    """Call the payoff-appropriate pricer.py wrapper. Raises only for
    programmer error (unreachable payoff values are filtered by callers
    before this is invoked). `pair` is passed through to every pricer.py
    call so T uses the calendar-aware year fraction and delta_convention/
    delta_premium_adjusted are populated -- see pricer.py / calendars.py
    (Phase 7). `inputs.vol` arrives already scaled by `vol_factor` (`cut_time_factor`);
    the factor itself is only needed to scale the smile a digital is priced on."""
    S = inputs.spot
    K = row["strike"]
    option_type = row["option_type"]
    dr, fr, vol = inputs.domestic_rate, inputs.foreign_rate, inputs.vol
    payoff = row["payoff"]

    if payoff == "VANILLA":
        return pricer.price_fx_vanilla(S, K, expiry, as_of_date, dr, fr, vol, option_type, pair=pair)
    if payoff == "DIGITAL":
        # cash_payout stays 1.0: trades.quantity is the payout notional, so PREMIUM comes
        # back as a fraction of the payout, the blotter fill's own unit.
        vol_at = _smile_vol_at(inputs)
        if vol_at is not None:
            # User decision 2026-09-21 ("yes, price off the smile"): a digital is a tight
            # call / put spread, so the slope of the smile is part of its price; one vol
            # read at the strike overstated the book's USDJPY 152 digital puts by points of
            # payout. With no smile on file (ATM interpolation, a manual vol) no slope is
            # known and the single-vol price below is all there is.
            return pricer.price_fx_digital_on_smile(
                S, K, expiry, as_of_date, dr, fr, lambda k: vol_at(k) * vol_factor, option_type,
                pair=pair, payout_ccy=CASH_PAYOUT_CCY)
        return pricer.price_fx_digital(S, K, expiry, as_of_date, dr, fr, vol, option_type, pair=pair,
                                       payout_ccy=CASH_PAYOUT_CCY)
    if payoff == "AMERICAN":
        return pricer.price_fx_american(S, K, expiry, as_of_date, dr, fr, vol, option_type, pair=pair)
    if payoff == "ASIAN":
        # avg_start_date read into `row` above but not passed through -- see
        # pricer.py::price_fx_asian's docstring for why (vendored engine has
        # no such parameter).
        return pricer.price_fx_asian(S, K, expiry, as_of_date, dr, fr, vol, option_type, pair=pair)
    if payoff in ("BARRIER_KI", "BARRIER_KO"):
        barrier_level = row["barrier_level"]
        # Derivation rule (task instruction: "derive from barrier vs spot"):
        # the vendored barrier pricer needs an explicit up/down direction
        # ('up-and-*' requires barrier > S, 'down-and-*' requires barrier <
        # S -- QuantLib's AnalyticBarrierEngine assumes this ordering and
        # gives nonsense otherwise). Barrier ABOVE current spot -> "up";
        # barrier BELOW spot -> "down". KI (knock-in) suffix '-and-in', KO
        # (knock-out) suffix '-and-out'.
        direction = "up" if barrier_level > S else "down"
        suffix = "-and-in" if payoff == "BARRIER_KI" else "-and-out"
        barrier_type = f"{direction}{suffix}"
        return pricer.price_fx_barrier(
            S, K, barrier_level, expiry, as_of_date, dr, fr, vol, option_type, barrier_type, pair=pair,
        )
    if payoff in ("ONE_TOUCH", "NO_TOUCH"):
        barrier_level = row["barrier_level"]
        # Same up/down derivation as the barrier branch above.
        direction = "up" if barrier_level > S else "down"
        fn = pricer.price_fx_one_touch if payoff == "ONE_TOUCH" else pricer.price_fx_no_touch
        return fn(S, barrier_level, expiry, as_of_date, dr, fr, vol, direction, pair=pair,
                  payout_ccy=CASH_PAYOUT_CCY)
    raise ValueError(f"unreachable payoff {payoff!r}")  # pragma: no cover


def _price_row(
    conn: sqlite3.Connection,
    as_of: str,
    row: dict,
    surface_cache: Optional[dict] = None,
    curve_cache: Optional[dict] = None,
    snapped: Optional[str] = None,
) -> PricingOutcome:
    """Price one trade as of `as_of` from that date's own inputs and write its marks dated
    `as_of`, stamped `snapped` -- the actual pricing time (`live_stamp`) when the caller
    passes none, a close stamp when `price_close` does."""
    terms_reason = _terms_skip_reason(row)
    if terms_reason:
        return _skip(row, terms_reason)

    as_of_date = datetime.date.fromisoformat(as_of)
    expiry = datetime.date.fromisoformat(row["expiry_date"])
    if expiry < as_of_date:
        return _skip(row, _expired_reason(row, as_of))

    stamp = snapped or live_stamp()
    pair = row["base_ccy"] + row["quote_ccy"]
    if expiry == as_of_date:
        # Expiry day: the payoff at today's official SPOT, never a model price (module
        # docstring, "Expiry day"). Rewritten by every pull of the day (INSERT OR REPLACE),
        # so its final value is the day's last official spot.
        return _intrinsic_outcome(conn, row, pair, mark_date=as_of, snapped=stamp)

    inputs_result = resolve_market_inputs(
        conn, as_of, pair, row["expiry_date"], strike=row["strike"],
        surface_cache=surface_cache, curve_cache=curve_cache,
    )
    if inputs_result.inputs is None:
        return _skip(row, inputs_result.reason)
    inputs = inputs_result.inputs

    factor = cut_time_factor(conn, as_of, pair, expiry)
    if factor != 1.0:
        result = _dispatch(row, as_of_date, expiry, dataclasses.replace(inputs, vol=inputs.vol * factor), pair,
                           vol_factor=factor)
        result = dataclasses.replace(result, vega=result.vega * factor)   # per point of the QUOTED vol
    else:
        result = _dispatch(row, as_of_date, expiry, inputs, pair)

    with conn:
        _insert_marks(conn, as_of, row, result, stamp)

    vol_source = inputs.vol_source
    if row["payoff"] == "DIGITAL" and _smile_vol_at(inputs) is not None:
        vol_source =dataclasses.replace(vol_source, detail=f"{vol_source.detail}; {SMILE_DIGITAL_DETAIL}")
    dom_rate_source = inputs.domestic_rate_source
    for_rate_source = inputs.foreign_rate_source
    return PricingOutcome(
        trade_id=row["trade_id"], instrument_id=row["instrument_id"], package_id=row["package_id"],
        quantity=row["quantity"], priced=True, result=result, raw=_raw(result),
        vol_source_kind=vol_source.source_kind if vol_source is not None else None,
        vol_detail=vol_source.detail if vol_source is not None else "",
        domestic_rate_source_kind=dom_rate_source.source_kind if dom_rate_source is not None else None,
        domestic_rate_detail=dom_rate_source.detail if dom_rate_source is not None else "",
        foreign_rate_source_kind=for_rate_source.source_kind if for_rate_source is not None else None,
        foreign_rate_detail=for_rate_source.detail if for_rate_source is not None else "",
        mark_basis="MODEL", mark_date=as_of,
    )


def _terms_skip_reason(row: dict) -> str:
    """'' when the option's own terms allow pricing, else the skip reason. Shared by the
    model path, the expiry-day mark and the catch-up, so all three refuse the same rows."""
    if row["product"] != "FX_OPTION":
        return f"product {row['product']!r} is not FX_OPTION"
    if row["payoff"] is None:
        return NO_STRIKE_REASON + " (no option terms recorded for this instrument at all)"

    payoff = row["payoff"]
    if payoff not in (_STRIKE_PAYOFFS | _BARRIER_PAYOFFS):
        return f"payoff {payoff!r} not supported"

    # A strike of 0 / NULL is the schema's "not known" sentinel (the blotter export
    # carries no strike for the book's digitals). Never priced at 0, at spot, or at any
    # other stand-in: skipped until the strike is typed in (`set_option_terms`).
    if payoff in _STRIKE_PAYOFFS and not (row["strike"] and row["strike"] > 0):
        return NO_STRIKE_REASON
    if payoff in _BARRIER_PAYOFFS and not (row["barrier_level"] and row["barrier_level"] > 0):
        return NO_BARRIER_REASON
    if payoff not in ("ONE_TOUCH", "NO_TOUCH") and row["option_type"] not in ("CALL", "PUT"):
        return f"no call/put on file (option_type is {row['option_type']!r})"
    return ""


def _expired_reason(row: dict, as_of: str) -> str:
    return f"expiry {row['expiry_date']} is not after as_of {as_of}"


def _raw(result: pricer.OptionPriceResult) -> Dict[str, float]:
    return {"price": result.quote_price, "delta": result.delta, "gamma": result.gamma,
            "theta": result.theta, "vega": result.vega, "rho": result.rho}


def _insert_marks(conn: sqlite3.Connection, mark_date: str, row: dict, result: pricer.OptionPriceResult,
                  snapped: str) -> None:
    """INSERT OR REPLACE the six marks (+ DELTA_PA) dated `mark_date`, every row stamped
    `snapped` (module docstring, "the stamp on a mark": the actual pricing time for a live
    run, the day's 15:00 New York close for a past close). No commit of its own: the caller
    owns the transaction (`with conn:`), so a catch-up can drop a stale frozen row in the
    same one."""
    settle_date = row["expiry_date"]
    values = {
        "PREMIUM": result.premium,
        "DELTA": result.delta,
        "GAMMA": result.gamma,
        "THETA": result.theta,
        "VEGA": result.vega,
        "RHO": result.rho,
    }
    mark_rows = [
        (mark_date, row["instrument_id"], settle_date, mark_type, values[mark_type], PRICER_SOURCE, snapped)
        for mark_type in _MARK_FIELDS
    ]
    # DELTA_PA (Phase 7): only for a pair whose market convention is
    # premium-adjusted delta (pricer.py::_delta_convention /
    # options_calc.fx.g10.recommended_delta) -- for a RAW-convention or
    # UNKNOWN (non-G10) pair, no DELTA_PA row is written; DELTA (raw spot
    # delta) is unconditionally written above regardless, unchanged from
    # Phase 2. Official under QL_OPTIONS_PRICER like the other six
    # (data/ingest/schema.py::OFFICIAL_MARK_SOURCE).
    if result.delta_convention == "PREMIUM_ADJUSTED":
        mark_rows.append(
            (mark_date, row["instrument_id"], settle_date, "DELTA_PA", result.delta_premium_adjusted,
             PRICER_SOURCE, snapped)
        )
    conn.executemany(
        "INSERT OR REPLACE INTO marks "
        "(as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at) "
        "VALUES (?,?,?,?,?,?,?)",
        mark_rows,
    )


def _intrinsic_outcome(conn: sqlite3.Connection, row: dict, pair: str, mark_date: str,
                       refreeze: bool = False, snapped: Optional[str] = None) -> PricingOutcome:
    """Write the option's PAYOFF at `pair`'s official SPOT of `mark_date` (= the expiry
    date) as its marks for that date -- module docstring, "Expiry day". Skips, writing
    nothing, for a path-dependent payoff and when that SPOT is not on file; never falls
    back to a model price and never writes 0 for "unknown". `refreeze` (the catch-up and
    `price_close`): in the same transaction, drop the instrument's `realised_pnl` rows
    frozen from a premium dated BEFORE the expiry date, so the ledger freezes them afresh.
    `snapped`: the stamp on the marks; none = the expiry date's 15:00 New York close (the
    catch-up reconstructs a past day), the live expiry-day run passes the pricing time."""
    if row["payoff"] in pricer.PATH_DEPENDENT_PAYOFFS:
        return _skip(row, f"expiry day: the payoff of {row['payoff']} depends on the path spot took "
                          "(a barrier touched, an average), which the closing spot alone cannot tell; "
                          "no expiry-day mark is written and no model price is used on expiry day")
    from .inputs import get_spot

    spot = get_spot(conn, mark_date, pair)
    if spot is None:
        return _skip(row, "no SPOT mark")
    result = pricer.price_fx_at_expiry(row["payoff"], spot, row["strike"], row["option_type"], pair=pair,
                                       payout_ccy=CASH_PAYOUT_CCY)
    with conn:
        _insert_marks(conn, mark_date, row, result, snapped or close_stamp(mark_date))
        if refreeze and _table_exists(conn, "realised_pnl"):
            conn.execute("DELETE FROM realised_pnl WHERE instrument_id = ? AND spot_as_of_date < ?",
                         (row["instrument_id"], mark_date))
    return PricingOutcome(
        trade_id=row["trade_id"], instrument_id=row["instrument_id"], package_id=row["package_id"],
        quantity=row["quantity"], priced=True, result=result, raw=_raw(result),
        mark_basis="INTRINSIC", mark_date=mark_date,
    )


def _catch_up_expiry_mark(conn: sqlite3.Connection, as_of: str, row: dict) -> Optional[PricingOutcome]:
    """For an option that expired BEFORE `as_of` and is not yet frozen from an
    expiry-dated mark, make the expiry-dated intrinsic marks agree with the official SPOT
    ON FILE NOW for the expiry date (module docstring, "Expiry day"):
      - no official PREMIUM dated the expiry date (the app did not run that day): write
        the intrinsic marks dated the expiry date from that date's official SPOT (the
        historical backfill writes closing SPOT rows);
      - one on file, but the payoff recomputed from the SPOT now on file for that date
        differs from it (the last live pull of expiry day saw 151.90, the close that
        later replaced it is 152.30): rewrite the marks from the SPOT on file. Compared
        on PREMIUM; nothing is rewritten when they agree.
    Either way the instrument's `realised_pnl` rows frozen from a premium dated BEFORE the
    expiry date go in the same transaction. Once a `realised_pnl` row frozen FROM an
    expiry-dated mark exists, mark and row are both left alone for good: the payoff is
    the one at the official spot on file at the moment of that first freeze.
    Returns the priced outcome when marks were (re)written; a skip naming the missing
    SPOT when there is no expiry-dated mark and none can be written; None when there is
    nothing to do -- not expired, terms incomplete, a path-dependent payoff, already
    frozen from an expiry-dated mark, or mark and SPOT on file already agree."""
    import math

    from .inputs import get_spot

    if _terms_skip_reason(row) or row["payoff"] not in pricer.EXPIRY_PAYOFFS:
        return None
    expiry_iso = row["expiry_date"]
    if not datetime.date.fromisoformat(expiry_iso) < datetime.date.fromisoformat(as_of):
        return None
    if _frozen_from_expiry_mark(conn, row):
        return None
    pair = row["base_ccy"] + row["quote_ccy"]
    stored = conn.execute(
        "SELECT value FROM marks_official WHERE instrument_id = ? AND mark_type = 'PREMIUM' AND as_of_date = ?",
        (row["instrument_id"], expiry_iso)).fetchone()
    if stored is not None:
        spot = get_spot(conn, expiry_iso, pair)
        if spot is None:
            return None  # no SPOT on file for that date to recompute from: the stored mark stands
        recomputed = pricer.price_fx_at_expiry(row["payoff"], spot, row["strike"], row["option_type"], pair=pair,
                                               payout_ccy=CASH_PAYOUT_CCY)
        if math.isclose(recomputed.premium, stored[0], rel_tol=1e-12, abs_tol=1e-15):
            return None
    outcome = _intrinsic_outcome(conn, row, pair, mark_date=expiry_iso, refreeze=True)
    if not outcome.priced:
        outcome.reason = (f"{_expired_reason(row, as_of)}; no expiry-day mark on file and none could be written: "
                          f"no official SPOT for {pair} on {expiry_iso}")
    return outcome


VALID_PAYOFFS = ("VANILLA", "DIGITAL", "AMERICAN", "ASIAN", "BARRIER_KI", "BARRIER_KO", "ONE_TOUCH", "NO_TOUCH")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _frozen_from_expiry_mark(conn: sqlite3.Connection, row: dict) -> bool:
    """Has the ledger frozen this instrument from an expiry-dated mark (`realised_pnl` row
    with `spot_as_of_date >= expiry`)? From then on the expiry-day mark and the row are
    left alone for good (module docstring, "Expiry day", the W-2 rule). No table: no."""
    return _table_exists(conn, "realised_pnl") and conn.execute(
        "SELECT 1 FROM realised_pnl WHERE instrument_id = ? AND spot_as_of_date >= ?",
        (row["instrument_id"], row["expiry_date"])).fetchone() is not None


def _same_terms(on_file: tuple, new: tuple) -> bool:
    """(strike, option_type, payoff, barrier_level) on file vs about to be written. Numbers
    compare to 1 part in 1e12 -- a strike that went through a UI text box and back is the
    same strike; no real change of strike or barrier is that small -- text case-blind."""
    import math

    def number(value) -> float:
        return float(value or 0.0)

    def text(value) -> str:
        return (value or "").strip().upper()

    return (math.isclose(number(on_file[0]), number(new[0]), rel_tol=1e-12, abs_tol=0.0)
            and text(on_file[1]) == text(new[1])
            and text(on_file[2]) == text(new[2])
            and math.isclose(number(on_file[3]), number(new[3]), rel_tol=1e-12, abs_tol=0.0))


def on_file_terms(conn: sqlite3.Connection, instrument_id: str) -> dict:
    """The terms currently in `instrument_options` for one option: `strike`,
    `option_type`, `payoff`, `barrier_level` (0.0 / '' / 'VANILLA' / 0.0 when there is no
    row). For a caller that edits ONE term and must hand the others back to
    `set_option_terms` unchanged -- that function writes every term it is given."""
    row = conn.execute(
        "SELECT strike, option_type, payoff, barrier_level FROM instrument_options WHERE instrument_id = ?",
        (instrument_id,)).fetchone()
    if row is None:
        return {"strike": 0.0, "option_type": "", "payoff": "VANILLA", "barrier_level": 0.0}
    return {"strike": row[0] or 0.0, "option_type": row[1] or "", "payoff": row[2] or "VANILLA",
            "barrier_level": row[3] or 0.0}


def set_option_terms(conn: sqlite3.Connection, instrument_id: str, strike: float, option_type: str,
                     payoff: str = "VANILLA", barrier_level: float = 0.0) -> None:
    """Record (or correct) an option's terms in `instrument_options`, for options whose
    blotter export does not carry them (a digital with no STRIKE clause in its
    Description, a barrier level, a mis-detected payoff). The next pricing run reads them.
    Validates: instrument must be an option on file; payoff in VALID_PAYOFFS; option_type
    CALL/PUT; strike > 0 for strike payoffs; barrier_level > 0 for barrier/touch payoffs.
    Raises ValueError with a plain message otherwise. Commits.

    **`payoff` is WRITTEN, not merged: leaving it out stores 'VANILLA'**, replacing
    whatever payoff was on file (same for `barrier_level` -> 0). The blotter export marks
    none of the book's digitals as digital (`FxOption Type` is '0' on every option row and
    the Description has no payoff word), so they arrive as payoff 'VANILLA', strike 0. A
    caller that only knows the strike -- an editable strike cell -- must therefore pass
    the payoff it means (`on_file_terms(conn, instrument_id)['payoff']` to keep the
    current one, 'DIGITAL' for a digital): a digital saved as VANILLA prices without
    any error, as a vanilla, at a fraction of its real value.

    **A terms CHANGE deletes the marks priced under the old terms (2026-09-18).** The
    Options table calls this and then `price_and_store`; when that reprice is skipped (no
    vol, no curve, no spot) the marks written under the OLD terms used to stay on file and
    stay official, so a digital kept a vanilla's premium and Greeks and P&L was computed
    from them. So, in the SAME transaction as the terms write: if strike, option type,
    payoff or barrier level differs from what was on file (or nothing was on file), every
    `marks` row of this instrument with `source = 'QL_OPTIONS_PRICER'` is deleted -- ALL
    as_of dates, all seven types (`PRICER_MARK_TYPES`), because every one of them was
    computed from terms that are no longer the option's. The next pricing run writes them
    afresh for its own date; until then the book shows the option UNPRICED with the skip
    reason, never a stale number. All four terms are compared because each can reach the
    pricer (the strike also picks the smile vol for a touch); the two cases that cannot
    move the price (call/put on a touch, a barrier on a non-barrier payoff) cost one
    reprice and can never leave a stale mark. A re-save that leaves all four identical
    deletes NOTHING (the UI re-saves unchanged terms routinely). Rows of any other source
    -- MANUAL above all, the user's own reconciliation marks -- are never touched.

    **Frozen realised P&L goes with them (2026-09-18, coordinator's decision).** Under
    exactly the same condition and in the same transaction, every `realised_pnl` row
    whose `instrument_id` is this instrument is deleted. Why: an expired option is frozen
    ONCE by `engine/pnl/ledger.py::realise_settled`, from the last official PREMIUM on or
    before expiry; a figure computed from a premium priced under the wrong terms is not a
    realised P&L, and nothing else would ever revisit it (the ledger only freezes trades
    that have no row yet). With the row gone, the ledger's next pass freezes the trade
    afresh from the corrected marks -- or names it as unrealisable ("no official PREMIUM
    ... on or before <expiry>") until a corrected PREMIUM dated on or before expiry is on
    file, which is the truth in the meantime. An identical
    re-save and a refused write delete nothing here either; another instrument's rows
    are never touched; a database with no `realised_pnl` table is skipped silently."""
    row = conn.execute("SELECT asset_class FROM instruments WHERE instrument_id = ?", (instrument_id,)).fetchone()
    if row is None or row[0] not in ("FX_OPTION", "EQ_OPTION", "CMDTY_OPTION"):
        raise ValueError(f"{instrument_id!r} is not an option instrument on file")
    # Formatting never refuses typed input (CLAUDE.md hard rule 6): case and padding are
    # normalised here exactly as `_same_terms` normalises them, so a re-save of the same
    # terms in any shape is neither refused nor taken for a change.
    payoff = ((payoff or "").strip() or "VANILLA").upper()
    if payoff not in VALID_PAYOFFS:
        raise ValueError(f"payoff must be one of {', '.join(VALID_PAYOFFS)}")
    option_type = (option_type or "").strip().upper()
    if option_type not in ("CALL", "PUT"):
        raise ValueError("option type must be CALL or PUT")
    strike = float(strike or 0.0)
    barrier_level = float(barrier_level or 0.0)
    if payoff in _STRIKE_PAYOFFS and strike <= 0:
        raise ValueError("strike must be greater than 0 for this payoff")
    if payoff in ("BARRIER_KI", "BARRIER_KO", "ONE_TOUCH", "NO_TOUCH") and barrier_level <= 0:
        raise ValueError("barrier / touch level must be greater than 0 for this payoff")
    with conn:  # one transaction: the terms write and the stale-mark delete land together or not at all
        before = conn.execute(
            "SELECT strike, option_type, payoff, barrier_level FROM instrument_options WHERE instrument_id = ?",
            (instrument_id,)).fetchone()
        conn.execute(
            "INSERT INTO instrument_options (instrument_id, strike, option_type, barrier_level, avg_start_date, payoff) "
            "VALUES (?, ?, ?, ?, '9999-12-31', ?) ON CONFLICT(instrument_id) DO UPDATE SET "
            "strike = excluded.strike, option_type = excluded.option_type, "
            "barrier_level = excluded.barrier_level, payoff = excluded.payoff",
            (instrument_id, strike, option_type, barrier_level, payoff))
        if before is None or not _same_terms(before, (strike, option_type, payoff, barrier_level)):
            placeholders = ",".join("?" * len(PRICER_MARK_TYPES))
            conn.execute(
                f"DELETE FROM marks WHERE instrument_id = ? AND source = ? AND mark_type IN ({placeholders})",
                (instrument_id, PRICER_SOURCE, *PRICER_MARK_TYPES))
            # The ledger's frozen rows for this instrument, same condition, same
            # transaction (docstring: "frozen realised P&L"). `realised_pnl` comes from
            # data/ingest/schema.py::create_schema; a database without it (one that has
            # never been through the app's startup) simply has nothing frozen to drop --
            # never a CREATE from here.
            if _table_exists(conn, "realised_pnl"):
                conn.execute("DELETE FROM realised_pnl WHERE instrument_id = ?", (instrument_id,))


# --------------------------------------------------------------------------- one-time migrations
# No meta / settings table exists anywhere in the schema (data/ingest/schema.py), so this
# package keeps its own tiny marker table, created defensively like `option_vols` and
# `manual_rates`: one row per migration that has run on this database.
_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS options_migrations (
  name        TEXT PRIMARY KEY,
  applied_at  TEXT NOT NULL
);
"""
CASH_PAYOFF_UNIT_PURGE = "2026-09-18-purge-cash-payoff-marks-written-in-the-old-unit"
_CASH_PAYOFFS = ("DIGITAL", "ONE_TOUCH", "NO_TOUCH")


def purge_old_unit_cash_payoff_marks(conn: sqlite3.Connection) -> dict:
    """ONE-TIME, idempotent. Until the 2026-09-18 units audit every DIGITAL / ONE_TOUCH /
    NO_TOUCH PREMIUM and Greek was written as "1 QUOTE unit per base unit of quantity,
    divided by spot": 1/S of the truth (1/150 on USDJPY). Those rows stay official for
    their own dates -- a re-pull only overwrites today's -- so Daily = LTD(today) -
    LTD(yesterday) would show a jump that never happened (about USD 420k on the book's
    three digitals) and 5d / MTD would carry it for weeks; and an identical re-save of
    the terms deletes nothing, so the user could not clear them.

    On its first call on a database this deletes, in ONE transaction: every
    QL_OPTIONS_PRICER mark (all seven `PRICER_MARK_TYPES`, all dates) of every FX_OPTION
    instrument whose payoff on file is DIGITAL, ONE_TOUCH or NO_TOUCH, those instruments'
    `realised_pnl` rows (frozen from such marks; table absent -> skipped), and records
    itself in `options_migrations` -- so it never runs again: marks written after it, in
    the right unit, survive every later call. Old-unit and new-unit rows cannot be told
    apart by value, which is why this is a run-once purge and not a filter. Never
    touched: VANILLA / AMERICAN / ASIAN / BARRIER marks (their unit was always right),
    MANUAL or any other source's rows, any other instrument. `price_all_and_store` calls
    it first, so the same pull rewrites today's marks in the right unit.
    Returns {'ran', 'instruments', 'marks_deleted', 'realised_deleted'}."""
    conn.execute(_MIGRATIONS_DDL)
    if conn.execute("SELECT 1 FROM options_migrations WHERE name = ?", (CASH_PAYOFF_UNIT_PURGE,)).fetchone():
        return {"ran": False, "instruments": [], "marks_deleted": 0, "realised_deleted": 0}
    payoffs = ",".join("?" * len(_CASH_PAYOFFS))
    types = ",".join("?" * len(PRICER_MARK_TYPES))
    with conn:
        # FX only: the old unit came from the FX wrappers (pricer.price_fx_digital / touch).
        # An equity digital's PREMIUM never went through a spot division.
        instruments = [r[0] for r in conn.execute(
            "SELECT o.instrument_id FROM instrument_options o JOIN instruments i USING (instrument_id) "
            f"WHERE i.asset_class = 'FX_OPTION' AND UPPER(o.payoff) IN ({payoffs}) ORDER BY o.instrument_id",
            _CASH_PAYOFFS).fetchall()]
        marks_deleted = realised_deleted = 0
        has_ledger = _table_exists(conn, "realised_pnl")
        for instrument_id in instruments:
            cur = conn.execute(
                f"DELETE FROM marks WHERE instrument_id = ? AND source = ? AND mark_type IN ({types})",
                (instrument_id, PRICER_SOURCE, *PRICER_MARK_TYPES))
            marks_deleted += max(cur.rowcount, 0)
            if has_ledger:
                cur = conn.execute("DELETE FROM realised_pnl WHERE instrument_id = ?", (instrument_id,))
                realised_deleted += max(cur.rowcount, 0)
        conn.execute("INSERT INTO options_migrations (name, applied_at) VALUES (?, ?)",
                     (CASH_PAYOFF_UNIT_PURGE, datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")))
    return {"ran": True, "instruments": instruments, "marks_deleted": marks_deleted,
            "realised_deleted": realised_deleted}


def price_and_store(conn: sqlite3.Connection, as_of: str, trade_id: str,
                    snapped: Optional[str] = None) -> PricingOutcome:
    """Price one FX_OPTION trade (read from ``trades_official``) as of
    ``as_of`` (ISO date string) and write its marks. Raises ValueError if no
    such trade exists; returns a PricingOutcome with ``priced=False`` (never
    raises) for every other reason a trade cannot be priced. ``as_of`` == the expiry
    date writes the PAYOFF at that day's official SPOT instead of a model price
    (``mark_basis='INTRINSIC'``); ``as_of`` after expiry is skipped, and the catch-up for
    a missed expiry day runs only in ``price_all_and_store`` (module docstring, "Expiry
    day"). ``snapped``: the marks' stamp; none = the actual pricing time (``live_stamp``)."""
    row = _read_option_trade(conn, trade_id)
    if row is None:
        raise ValueError(f"No trade {trade_id!r} in trades_official")
    # Fresh, single-trade surface/curve cache -- no reuse across calls, but
    # keeps the same code path as price_all_and_store below.
    return _price_row(conn, as_of, row, surface_cache={}, curve_cache={}, snapped=snapped)


def price_all_and_store(conn: sqlite3.Connection, as_of: str, snapped: Optional[str] = None) -> List[PricingOutcome]:
    """Price every FX_OPTION trade in ``trades_official`` as of ``as_of`` -- the live
    pull's step. Every trade on file is priced, whatever its trade_date: a mark dated
    before a trade's own trade date never enters a valuation (``engine/pnl/valuation.py``
    reads trades with ``trade_date <= as_of``), and a trade dealt in Asia after the New
    York day has rolled, dated tomorrow, still needs today's mark for tomorrow's book to
    be priced off (the near-marks rule). Marks are stamped ``snapped``, none = the actual
    pricing time (``live_stamp``), so a re-pull visibly replaces the day's marks: same key,
    new value, newer stamp (INSERT OR REPLACE, never a second row).
    One `surface_cache` dict is shared across the whole run (see
    inputs.py::_cached_surface) so that N trades on the same pair build
    that pair's FXDeltaVolSurface once, not N times; likewise one
    `curve_cache` dict (see engine/options/rates.py) so N trades sharing a
    currency build that currency's OIS bootstrap once, not N times.

    Also the expiry-day CATCH-UP (module docstring, "Expiry day"): an option that expired
    before ``as_of`` with no expiry-day mark on file gets its intrinsic marks written
    DATED ITS EXPIRY DATE (``PricingOutcome.mark_date``), from that date's official SPOT,
    and any `realised_pnl` row frozen from an older premium is dropped so the ledger
    freezes it afresh. Such an outcome comes back ``priced=True, mark_basis='INTRINSIC'``;
    every other expired option stays the usual skip.

    A trade of an option closed out as of ``as_of`` ("Closed-out options" above) is
    neither priced nor caught up: it comes back ``priced=False, closed_out=True`` with
    its reason, its marks on file untouched.

    Then the options on commodity futures open on ``as_of`` (``price_listed_options``), their
    outcomes appended after the FX ones with ``product='CMDTY_OPTION'``.

    First of all, once per database: `purge_old_unit_cash_payoff_marks` (digital / touch
    marks written in the pre-2026-09-18 unit), so the first pull after a restart clears
    them and this same run rewrites today's in the right unit."""
    purged = purge_old_unit_cash_payoff_marks(conn)
    if purged["ran"] and purged["marks_deleted"]:
        import logging
        logging.getLogger(__name__).warning(
            "one-time purge of digital / touch marks written in the pre-2026-09-18 unit: %d marks and %d "
            "realised_pnl rows deleted for %s", purged["marks_deleted"], purged["realised_deleted"],
            ", ".join(purged["instruments"]))
    trade_ids = [
        r[0] for r in conn.execute(
            "SELECT trade_id FROM trades_official WHERE product = 'FX_OPTION' ORDER BY trade_id"
        ).fetchall()
    ]
    closed = closed_out_options(conn, as_of)
    surface_cache: dict = {}
    curve_cache: dict = {}
    outcomes = []
    for trade_id in trade_ids:
        row = _read_option_trade(conn, trade_id)
        if row and trade_id in closed:
            outcomes.append(_closed_out_skip(row, closed[trade_id]))
            continue
        try:
            outcome = _price_row(conn, as_of, row, surface_cache=surface_cache, curve_cache=curve_cache,
                                 snapped=snapped)
            if not outcome.priced:
                # Expired before `as_of` with no expiry-day mark (the app did not run that
                # day): write it now from the expiry date's own official SPOT, if there is
                # one -- module docstring, "Expiry day". None = nothing to catch up.
                caught_up = _catch_up_expiry_mark(conn, as_of, row)
                if caught_up is not None:
                    outcome = caught_up
            outcomes.append(outcome)
        except Exception as exc:  # noqa: BLE001
            # One option's pricer blowing up (an edge-case payoff / vol / rate combination
            # inside the vendored QuantLib wrappers) must not zero the whole options step
            # for every other trade in the book: before 2026-09-18 the exception escaped
            # to live._options_step's step-level guard and no option priced that cycle.
            # The failing trade is reported like any other skip, with the error text.
            reason = f"pricer error: {exc!r}"
            if row:
                outcomes.append(_skip(row, reason))
            else:
                outcomes.append(PricingOutcome(trade_id=trade_id, instrument_id="", package_id=trade_id,
                                               quantity=0.0, priced=False, reason=reason))
    outcomes.extend(price_listed_options(conn, as_of, snapped=snapped))
    return outcomes


# --------------------------------------------------------------------------- options on commodity futures (2026-09-24)
# Commodity conversion Phase 5 (CLAUDE.md "P&L conventions -> Options on commodity futures"):
# a CMDTY_OPTION's P&L is Bloomberg's own price of it (official FUTURE_PX, read by engine/pnl),
# so no model enters it; what the bulk passes write for it are the Greeks, Black-76 or
# Barone-Adesi-Whaley at the vol that price implies (`equity_commodity.
# price_listed_commodity_option`, listed-options-pricer's), under QL_OPTIONS_PRICER at the
# option's expiry key, stamped like the FX marks of the same pass (the pricing time live, the
# day's 15:00 New York close for a past close). What differs from the FX options:
#   * in the pass: a trade dealt on or before the date priced whose NOTIONAL leg (its expiry,
#     else the instrument's) has not settled before it; an expired one is simply not listed;
#   * each instrument is priced ONCE however many trades are on it (a buy and its sell-back
#     sit on one canonical id), and each of its trades gets its own outcome;
#   * no expiry-day payoff and no catch-up: the expiry-day rule is the FX PREMIUM's; a listed
#     option's freeze is the ledger's, at Bloomberg's last price. On its expiry date the pricer
#     leaves the Greeks blank with its reason ("expiry ... is not after as_of ...");
#   * a closed-out group (`closed_out_listed_options`) is left unpriced and reported under
#     `closed_out`, as for FX.

LISTED_PRODUCT = "CMDTY_OPTION"

# Terms rows in the shape `engine.pnl.valuation._opt_sql` feeds `closed_out_from_rows`.
_LISTED_TERMS_SQL = """
SELECT t.trade_id, t.instrument_id, t.product, '' AS strategy, '' AS theme,
       t.trade_date, t.quantity, t.price AS fill, i.base_ccy, i.quote_ccy,
       COALESCE(l.settle_date, i.expiry_date) AS settle_date,
       COALESCE(o.strike, 0) AS strike, COALESCE(o.option_type, '') AS option_type,
       COALESCE(o.payoff, 'VANILLA') AS payoff, COALESCE(o.barrier_level, 0) AS barrier_level,
       COALESCE(o.avg_start_date, '9999-12-31') AS avg_start_date
FROM trades_official t
JOIN instruments i ON i.instrument_id = t.instrument_id
LEFT JOIN trade_legs l ON l.trade_id = t.trade_id AND l.leg_no = 1
LEFT JOIN instrument_options o ON o.instrument_id = t.instrument_id
WHERE t.product = :product AND t.trade_date <= :as_of
ORDER BY t.trade_id
"""

# The day's book of options on futures: dealt on or before `day`, expiry (the NOTIONAL leg's
# settle date, else the instrument's) on or after it.
_LISTED_BOOK_SQL = """
SELECT t.trade_id, t.instrument_id, t.product, t.package_id, t.quantity
FROM trades_official t
JOIN instruments i ON i.instrument_id = t.instrument_id
LEFT JOIN trade_legs l ON l.trade_id = t.trade_id AND l.leg_type = 'NOTIONAL'
WHERE t.product = :product AND t.trade_date <= :day
GROUP BY t.trade_id
HAVING COALESCE(MAX(l.settle_date), MAX(i.expiry_date)) >= :day
ORDER BY t.trade_id
"""


def _listed_outcome(row: dict, priced: dict, as_of: str) -> PricingOutcome:
    """One trade's outcome from its instrument's `price_listed_commodity_option` dict."""
    ok = bool(priced.get("marks"))
    outcome = PricingOutcome(
        trade_id=row["trade_id"], instrument_id=row["instrument_id"], package_id=row["package_id"],
        quantity=row["quantity"], priced=ok, reason="" if ok else (priced.get("reason") or "not priced"),
        result=priced.get("result") if ok else None, product=LISTED_PRODUCT,
        implied_vol=priced.get("implied_vol") if ok else None,
    )
    if ok:
        outcome.raw = _raw(outcome.result)
        outcome.vol_source_kind = "IMPLIED"
        outcome.vol_detail = (f"implied by Bloomberg's price {priced['option_price']:g} of {row['instrument_id']} "
                              f"with {priced['underlying_id']} at {priced['underlying_price']:g} "
                              f"({priced['model']})")
        outcome.domestic_rate_detail = priced.get("rate_source") or ""
        outcome.mark_basis, outcome.mark_date = "MODEL", as_of
    return outcome


def _listed_skip(row: dict, reason: str) -> PricingOutcome:
    outcome = _skip(row, reason)
    outcome.product = LISTED_PRODUCT
    return outcome


def price_listed_options(conn: sqlite3.Connection, as_of: str, snapped: Optional[str] = None
                         ) -> List[PricingOutcome]:
    """Greeks of every option on a commodity future open on `as_of` ("Options on commodity
    futures" above), from `as_of`'s own marks, written under QL_OPTIONS_PRICER at each option's
    expiry key stamped `snapped` (none = the pricing time, `live_stamp`). One PricingOutcome per
    trade, `product='CMDTY_OPTION'`; each instrument priced once. A closed-out group is
    `closed_out=True`, never priced; a missing input (no option price, no underlying price, a
    price below intrinsic) is `priced=False` with the pricer's reason. Never raises: one
    instrument's pricer error is its trades' skip, and a failure outside the loop skips every
    trade not yet reported with the error."""
    from .equity_commodity import price_listed_commodity_option, write_listed_marks

    rows = [dict(zip(("trade_id", "instrument_id", "product", "package_id", "quantity"), r))
            for r in conn.execute(_LISTED_BOOK_SQL, {"product": LISTED_PRODUCT, "day": as_of}).fetchall()]
    if not rows:
        return []
    outcomes: List[PricingOutcome] = []
    try:
        closed = closed_out_listed_options(conn, as_of)
        stamp = snapped or live_stamp()
        curve_cache: dict = {}
        done: Dict[str, dict] = {}
        for row in rows:
            if row["trade_id"] in closed:
                outcome = _closed_out_skip(row, closed[row["trade_id"]])
                outcome.product = LISTED_PRODUCT
                outcomes.append(outcome)
                continue
            inst = row["instrument_id"]
            if inst not in done:
                try:
                    priced = price_listed_commodity_option(conn, inst, as_of, curve_cache)
                    write_listed_marks(conn, priced, snapped=stamp)
                except Exception as exc:  # noqa: BLE001 -- one option's blow-up is its trades' skip
                    priced = {"marks": {}, "reason": f"pricer error: {exc!r}"}
                done[inst] = priced
            outcomes.append(_listed_outcome(row, done[inst], as_of))
    except Exception as exc:  # noqa: BLE001 -- never zero the sibling outcomes (2026-09-22 loop guard)
        reported = {o.trade_id for o in outcomes}
        outcomes.extend(_listed_skip(r, f"pricer error: {exc!r}") for r in rows if r["trade_id"] not in reported)
    return outcomes


# --------------------------------------------------------------------------- past closes (2026-09-22)

_CLOSE_BOOK_SQL = """
SELECT t.trade_id
FROM trades_official t
JOIN instruments i ON i.instrument_id = t.instrument_id
WHERE t.product = 'FX_OPTION' AND t.trade_date <= :day AND i.expiry_date >= :day
ORDER BY t.trade_id
"""


ALL_PRODUCTS = ("FX_OPTION", LISTED_PRODUCT)


def price_close(conn: sqlite3.Connection, day: str, products: tuple = ALL_PRODUCTS) -> dict:
    """Price `day`'s CLOSE for every FX_OPTION trade in that day's book -- dealt on or
    before `day` (``trades.trade_date <= day``) and not expired before it
    (``instruments.expiry_date >= day``) -- strictly from `day`'s own inputs on file, and
    write the marks dated `day`. For the backfill to call for each day it works, after that
    day's closes (SPOT, forwards, OIS quotes, vol smile) are on file (module docstring,
    "Past closes and the stamp on a mark"; user, 2026-09-22: "options daily pnl 0, that
    cannot be right, everything is moving").

    Inputs: the pair's official SPOT of `day`, the OIS ``curve_quotes`` of `day` (or the
    covered-interest-parity rate off `day`'s own SPOT + FWD_OUTRIGHT marks), the
    ``vol_quotes`` smile of `day` (else its ATM interpolation, else a manual vol dated
    `day`) -- every resolver reads its own date's rows exactly (``inputs.get_spot``,
    ``vol_marketdata.vol_smile`` / ``atm_vol_for_expiry``, ``inputs.get_manual_vol``,
    ``rates._read_curve_quotes`` / ``_get_manual_rate`` / ``_get_fwd_outright_points``),
    so nothing of another day can leak in: an input missing on `day` skips the trade,
    reason "<day> close: <what is missing>", never a later day's vol, curve or spot.
    ``cut_time_factor`` reads the pricing moment off that day's SPOT stamp (15:00 New York
    for a close row), so the hours to the cut are the close's.

    Expiry on `day`: the payoff at `day`'s official SPOT ("Expiry day" rule), and the
    instrument's ``realised_pnl`` rows frozen from a premium dated before the expiry are
    dropped in the same transaction so the ledger's next pass freezes them from it (the
    catch-up's own rule) -- unless the ledger has ALREADY frozen the trade from an
    expiry-dated mark, in which case mark and row are left alone (W-2) and the trade is
    listed as skipped saying so. Expired before `day`: not in that day's book, left to
    ``price_all_and_store``'s catch-up. Dealt after `day`: not in that day's book, neither
    priced nor listed.

    Marks: the seven ``PRICER_MARK_TYPES`` under ``QL_OPTIONS_PRICER``, dated `day`,
    INSERT OR REPLACE (a re-run overwrites the same keys, never a second row), stamped
    `day`'s 15:00 New York close with the offset resolved for the date (``close_stamp``).
    One short transaction per trade, as ``price_all_and_store``. Idempotent. The one-time
    unit purge runs first, as there, so it can never eat what this writes.

    A trade of an option closed out as of `day` ("Closed-out options" above) is not priced,
    expiry on `day` included, and is listed under ``"closed_out"``, never ``"skipped"``.

    Options on commodity futures (2026-09-24, "Options on commodity futures" above): the
    CMDTY_OPTION trades open on `day` get their Greeks from `day`'s own FUTURE_PX of the option
    and of its underlying future (and `day`'s discount curve), stamped the same close, each
    instrument once, a closed-out group under ``"closed_out"``, a missing input under
    ``"skipped"`` with the day prefixed; no expiry-day payoff for them. `products` limits the
    pass (``recalc_on_file`` visits a day only for the products it has data for); the
    default is both.

    Never raises. Returns ``{"day": day, "priced": <int>, "skipped": [{"trade_id",
    "reason"}, ...], "closed_out": [<trade_id>, ...], "futures_options_priced": <int>}``
    (``priced`` counts both products, ``futures_options_priced`` the CMDTY_OPTION trades among
    them); an exception outside one trade's pricing adds ``"error"`` (its repr) and the counts
    so far; one trade's pricer error is its own skip."""
    out: dict = {"day": day, "priced": 0, "skipped": [], "closed_out": [], "futures_options_priced": 0}
    try:
        datetime.date.fromisoformat(day)
        purge_old_unit_cash_payoff_marks(conn)
        trade_ids = ([r[0] for r in conn.execute(_CLOSE_BOOK_SQL, {"day": day}).fetchall()]
                     if "FX_OPTION" in products else [])
        closed = closed_out_options(conn, day) if trade_ids else {}
        stamp = close_stamp(day)
        surface_cache: dict = {}
        curve_cache: dict = {}
        for trade_id in trade_ids:
            row = _read_option_trade(conn, trade_id)
            if row is None:  # pragma: no cover -- the id came from trades_official a moment ago
                out["skipped"].append({"trade_id": trade_id, "reason": f"{day} close: trade not on file"})
                continue
            if trade_id in closed:
                out["closed_out"].append(trade_id)
                continue
            try:
                if row["expiry_date"] == day:
                    terms_reason = _terms_skip_reason(row)
                    if terms_reason:
                        outcome = _skip(row, terms_reason)
                    elif _frozen_from_expiry_mark(conn, row):
                        outcome = _skip(row, "expiry day: the payoff is already frozen in realised P&L from the "
                                             "expiry-dated mark; mark and frozen row are left as they stand")
                    else:
                        pair = row["base_ccy"] + row["quote_ccy"]
                        outcome = _intrinsic_outcome(conn, row, pair, mark_date=day, refreeze=True, snapped=stamp)
                else:
                    outcome = _price_row(conn, day, row, surface_cache=surface_cache, curve_cache=curve_cache,
                                         snapped=stamp)
            except Exception as exc:  # noqa: BLE001 -- one trade's blow-up is its own skip, as in price_all_and_store
                outcome = _skip(row, f"pricer error: {exc!r}")
            if outcome.priced:
                out["priced"] += 1
            else:
                out["skipped"].append({"trade_id": trade_id, "reason": f"{day} close: {outcome.reason}"})
        if LISTED_PRODUCT in products:
            for outcome in price_listed_options(conn, day, snapped=stamp):
                if outcome.closed_out:
                    out["closed_out"].append(outcome.trade_id)
                elif outcome.priced:
                    out["priced"] += 1
                    out["futures_options_priced"] += 1
                else:
                    out["skipped"].append({"trade_id": outcome.trade_id, "reason": f"{day} close: {outcome.reason}"})
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{exc!r}"
    return out


# The past days whose logged data can price an option: every day before `as_of` (and from
# `since` on) with an official SPOT for a pair some FX_OPTION on file is written on. SPOT is
# the one input every payoff needs (the forward curve, OIS quotes and vols of the same day
# are then resolved, or the trade skips with its reason), so a day with none has nothing
# to recalculate from and is not visited.
_DAYS_WITH_OPTION_SPOT_SQL = """
SELECT DISTINCT m.as_of_date
FROM marks_official m
WHERE m.mark_type = 'SPOT' AND m.as_of_date < :as_of AND m.as_of_date >= :since
  AND m.instrument_id IN (
      SELECT DISTINCT i.base_ccy || i.quote_ccy
      FROM trades_official t JOIN instruments i ON i.instrument_id = t.instrument_id
      WHERE t.product = 'FX_OPTION')
ORDER BY m.as_of_date
"""

# The past days on which one option on a future has Bloomberg's own price on file.
_DAYS_WITH_PRICE_SQL = """
SELECT DISTINCT as_of_date FROM marks_official
WHERE instrument_id = :instrument AND mark_type = 'FUTURE_PX' AND as_of_date < :as_of AND as_of_date >= :since
"""


def _listed_days_on_file(conn: sqlite3.Connection, as_of: str, since: str) -> List[str]:
    """The past days (before `as_of`, from `since` on) on which some option on a future on
    file has both its own FUTURE_PX and its underlying future's: the inputs its Greeks start
    from (the discount curve of the day is then resolved, or the trade skips with its reason).
    An option whose underlying the contract master cannot name is visited on its own price's
    days, so the reason is reported rather than the option silently never priced."""
    try:
        from data.contracts import option_for   # lazy, as equity_commodity's own read
    except ImportError:  # pragma: no cover -- no contract master: every option on its own price's days
        option_for = None

    days: set = set()
    for inst, root in conn.execute(
            "SELECT DISTINCT i.instrument_id, i.base_ccy FROM trades_official t "
            "JOIN instruments i ON i.instrument_id = t.instrument_id WHERE t.product = ?",
            (LISTED_PRODUCT,)).fetchall():
        params = {"as_of": as_of, "since": since}
        own = {r[0] for r in conn.execute(_DAYS_WITH_PRICE_SQL, {**params, "instrument": inst}).fetchall()}
        try:
            if option_for is None:
                raise KeyError("no contract master")
            underlying = option_for(root, inst, conn=conn).underlying.contract_id
        except (KeyError, ValueError):
            days |= own
            continue
        days |= own & {r[0] for r in conn.execute(_DAYS_WITH_PRICE_SQL,
                                                   {**params, "instrument": underlying}).fetchall()}
    return sorted(days)


def recalc_on_file(conn: sqlite3.Connection, as_of: str, since: Optional[str] = None) -> dict:
    """Re-price every FX option from the data ON FILE, asking Bloomberg nothing (user,
    2026-09-22: "pull bbg now should recalc options too, using log data if no bbg access, or
    pull new data for new calculation"). For the pull button's no-Bloomberg branch
    (``data/bloomberg/live.py``, bbg-data's to wire): on a PC without a terminal the logged
    data -- the imported snapshot's SPOT, forwards, OIS quotes and vols, day by day -- is
    all on file, so the option marks can be rebuilt from it; with a terminal the connected
    path already re-prices after its own pull (``live.pull_once``'s options step).

    Runs ``price_close(conn, day)`` for every day before `as_of`, from `since` (default:
    the earliest FX_OPTION trade_date on file) on, that has an official SPOT for a pair
    some option is written on (``_DAYS_WITH_OPTION_SPOT_SQL``), each strictly from its
    own inputs; then ``price_all_and_store(conn, as_of)`` for `as_of` itself, stamped the
    pricing time, which also runs the expiry-day catch-up. A day whose inputs are
    incomplete skips its trades with the reason, as those two do; nothing is ever priced
    from another day's data (hard rule 2 stays with ``engine/pnl/valuation.py``'s
    near-marks rule at read time). Idempotent: every day is INSERT OR REPLACE.

    Options on commodity futures (2026-09-24): a past day is also visited when some
    CMDTY_OPTION on file has that day's FUTURE_PX of the option and of its underlying future
    (``_listed_days_on_file``); each day is priced only for the products it has data for
    (``price_close(..., products=...)``), so an FX-only day does not list every option on a
    future as "no price". `since` defaults to the earliest trade date of either product.

    Never raises. Returns ``{"as_of", "since", "days": [<price_close dict per day, the
    last one as_of's own {"day", "priced", "skipped", "closed_out", "futures_options_priced"}>],
    "priced": <total>, "skipped": <total count>, "closed_out": <total count of closed-out
    trades not priced, over every day>, "futures_options_priced": <total, the CMDTY_OPTION
    trades among "priced">}``, plus ``"error"`` (repr) if something outside the per-day calls
    raised, with the counts so far."""
    out: dict = {"as_of": as_of, "since": since, "days": [], "priced": 0, "skipped": 0, "closed_out": 0,
                 "futures_options_priced": 0}
    try:
        datetime.date.fromisoformat(as_of)
        if since is None:
            row = conn.execute("SELECT MIN(trade_date) FROM trades_official WHERE product IN (?, ?)",
                               ALL_PRODUCTS).fetchone()
            since = row[0] if row and row[0] else as_of
            out["since"] = since
        fx_days = {r[0] for r in conn.execute(_DAYS_WITH_OPTION_SPOT_SQL, {"as_of": as_of, "since": since}).fetchall()}
        listed_days = set(_listed_days_on_file(conn, as_of, since))
        for day in sorted(fx_days | listed_days):
            products = tuple(p for p, has in (("FX_OPTION", day in fx_days), (LISTED_PRODUCT, day in listed_days))
                             if has)
            result = price_close(conn, day, products=products)
            out["days"].append(result)
            out["priced"] += result["priced"]
            out["skipped"] += len(result["skipped"])
            out["closed_out"] += len(result.get("closed_out", []))
            out["futures_options_priced"] += result.get("futures_options_priced", 0)
        outcomes = price_all_and_store(conn, as_of)
        today = {"day": as_of, "priced": sum(1 for o in outcomes if o.priced),
                 "skipped": [{"trade_id": o.trade_id, "reason": o.reason}
                             for o in outcomes if not o.priced and not o.closed_out],
                 "closed_out": [o.trade_id for o in outcomes if o.closed_out],
                 "futures_options_priced": sum(1 for o in outcomes if o.priced and o.product == LISTED_PRODUCT)}
        out["days"].append(today)
        out["priced"] += today["priced"]
        out["skipped"] += len(today["skipped"])
        out["closed_out"] += len(today["closed_out"])
        out["futures_options_priced"] += today["futures_options_priced"]
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{exc!r}"
    return out
