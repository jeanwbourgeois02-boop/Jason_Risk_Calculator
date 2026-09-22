"""IRS pricing engine: QuantLib OIS curve bootstrap + swap valuation, ported from the
standalone reference project "Rates Swap Calculator" (``swapcalc/pricing/``), scoped
down to Phase 1 -- single-currency OIS only (USD SOFR, EUR ESTR, GBP SONIA, JPY TONA,
CHF SARON, CAD CORRA, AUD AONIA). Term-rate, basis and cross-currency (XCCY) curves and
instruments from the reference project are NOT ported; see each module's docstring for
what was dropped.

Note on docs: the original task brief for this package referenced
``docs/pricing_conventions.md``. That file does not exist in this repository (it exists
only in the standalone reference project) and none is being added here -- this
docstring plus the per-module docstrings below are the conventions record for this
package. Do not reference ``docs/pricing_conventions.md`` as if it exists in this repo.

Module map
----------
- ``errors.py``    -- exception types, ported verbatim.
- ``qlmap.py``      -- convention-string -> QuantLib object mapping, ported and
                       trimmed to OIS/RFR indices only.
- ``conventions.py`` -- the seven OIS index conventions (calendar, day counts,
                       payment/fixing/spot lag, ...), transcribed from the reference
                       project's ``conventions.yaml`` OIS entries. Hard-coded (no yaml
                       file in this repo) -- see module docstring.
- ``curves.py``     -- ``CurveSet`` + ``build_curve_set``: bootstraps one
                       self-discounted OIS curve from a list of ``(tenor, value)``
                       quotes, flat forwards (log-linear discount) by default since
                       2026-09-22 (user decision; see its docstring for why).
- ``instruments.py`` -- ``build_instrument``: IRS trade fields -> a QuantLib
                       ``OvernightIndexedSwap`` with a ``DiscountingSwapEngine``.
- ``valuation.py``  -- ``price_swap``: NPV, par rate, parallel + per-tenor-bucket DV01.
- ``store.py``      -- ``bootstrap_and_store`` / ``price_and_store``: SQLite glue
                       reading ``curve_quotes`` / ``trades`` / ``trade_legs`` and
                       writing ``curves`` / ``marks`` rows with ``source='QL_PRICER'``.

Sign convention (binding across this package; do not invent a different one ad hoc)
-------------------------------------------------------------------------------------
- ``trades.quantity > 0`` means **pay fixed** (CLAUDE.md "Leg layouts": "a payer
  (quantity > 0) has a negative FIXED leg (pays) and a positive FLOAT leg (receives)"),
  matching ``data/ingest/irs.py``'s documented BNP-``Position``-sign convention.
- ``instruments.build_instrument`` takes ``pay_fixed: bool`` (= ``quantity > 0``
  at the call site in ``store.py``) and maps it to ``ql.Swap.Payer`` /
  ``ql.Swap.Receiver``. NPV then follows QuantLib's own Payer/Receiver sign: positive
  NPV = asset to the fund.
- ``PV_USD`` mark = that NPV directly. ``DV01_USD`` mark = the NPV change for a +1bp
  (``valuation.BUMP = 1e-4``) parallel bump of every curve quote, i.e.
  ``NPV(bumped) - NPV(base)``.
- Per CLAUDE.md P&L conventions: ``PnL_USD = PV_USD(t) - PV_USD(trade date)``. This
  package only writes ``PV_USD`` marks; the subtraction is the P&L engine's job, not
  this package's.
- **Known gap** (see ``store.py`` docstring and agent memory
  ``rates-fx-conversion-gap.md``): Phase 1 has no FX-spot source wired in, so
  ``PV_USD``/``DV01_USD`` for a non-USD-notional IRS are actually PV/DV01 in the
  swap's own notional currency, not USD. Every IRS in the current reference data is
  USD notional, so this does not bite today, but it is a real limitation, not an
  oversight to be silently assumed away.

Official marks
--------------
Per ``data/ingest/schema.py``'s ``OFFICIAL_MARK_SOURCE`` (changed 2026-09-15,
authorized by the housekeeper): ``PAR_RATE`` / ``PV_USD`` / ``DV01_USD`` are official
from source ``QL_PRICER`` (this package). ``BBG_BDH`` is reconciliation-only for these
three mark_types now, mirroring how ``BNP_BVAL`` is reconciliation-only for FX.
"""
from .curves import CurveSet, build_curve_set
from .instruments import BuiltSwap, build_instrument
from .store import (bootstrap_and_store, load_fixings, price_all_and_store, price_and_store, recalc_on_file,
                    reverse_direction_marks, snapped_at)
from .valuation import SwapResult, price_swap

__all__ = [
    "CurveSet",
    "build_curve_set",
    "BuiltSwap",
    "build_instrument",
    "SwapResult",
    "price_swap",
    "bootstrap_and_store",
    "price_and_store",
    "price_all_and_store",
    "recalc_on_file",
    "reverse_direction_marks",
    "load_fixings",
    "snapped_at",
]
