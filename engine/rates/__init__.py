"""OIS discount curves: the QuantLib bootstrap the option pricers read, ported from the
standalone reference project "Rates Swap Calculator" (``swapcalc/pricing/``) and scoped
to single-currency OIS (USD SOFR, EUR ESTR, GBP SONIA, JPY TONA, CHF SARON, CAD CORRA,
AUD AONIA). Term-rate, basis and cross-currency curves are not ported.

Since 2026-09-24 (commodity conversion Phase 2; the user approved that rates / IRS leave
the app) this package prices no swap and writes no mark. The swap instrument builder
(``instruments.py``), the swap valuation (``valuation.py``: NPV, par rate, DV01) and the
store's swap entry points (``price_and_store``, ``price_all_and_store``,
``recalc_on_file``, ``reverse_direction_marks``, ``load_fixings``) were removed; git
history keeps them. No ``PAR_RATE`` / ``PV_USD`` / ``DV01_USD`` / ``CASHFLOW_USD`` mark is
written by anything here any more.

Note on docs: ``docs/pricing_conventions.md`` exists only in the standalone reference
project, not in this repository; this docstring plus the per-module docstrings are the
conventions record for this package.

Module map
----------
- ``errors.py``      -- exception types (``PricingError``, ``PricingConfigError``,
                        ``CurveBuildError``).
- ``qlmap.py``       -- convention-string -> QuantLib object mapping (calendars,
                        periods, business-day conventions, the overnight index).
- ``conventions.py`` -- the seven OIS index conventions and ``CCY_RFR`` (currency ->
                        overnight index), read by ``engine/options/rates.py`` too.
- ``curves.py``      -- ``CurveSet`` + ``build_curve_set``: bootstraps one
                        self-discounted OIS curve from ``(tenor, value)`` quotes.
                        Flat forwards (log-linear discount factors,
                        ``DEFAULT_INTERPOLATION``, user decision 2026-09-22); a
                        bootstrap that does not converge raises ``CurveBuildError``,
                        never a silent curve and never a fallback.
- ``store.py``       -- ``bootstrap_and_store`` (``curve_quotes`` -> ``curves`` rows,
                        ``source='QL_PRICER'``) and ``snapped_at`` (the 15:00 New York
                        close stamp).

Readers outside this package: ``data/bloomberg/live.py`` (``store.bootstrap_and_store``),
``engine/options/rates.py`` (``curves.build_curve_set`` / ``CurveSet``,
``conventions.CCY_RFR``), and ``store.snapped_at`` in ``engine/options/store.py``,
``engine/options/equity_commodity.py`` and ``data/bloomberg/vol_marketdata.py``.
"""
from .curves import CurveSet, build_curve_set
from .store import bootstrap_and_store, snapped_at

__all__ = [
    "CurveSet",
    "build_curve_set",
    "bootstrap_and_store",
    "snapped_at",
]
