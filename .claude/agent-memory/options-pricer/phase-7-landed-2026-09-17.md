---
name: phase-7-landed-2026-09-17
description: What Phase 7 (real rates, calendars, equity/commodity, portfolio) shipped -- read before touching engine/options/rates.py, calendars.py, equity_commodity.py, or portfolio.py.
metadata:
  type: project
---

Phase 7 of the options_calc merge landed 2026-09-17: `engine/options/rates.py`
(new), `engine/options/calendars.py` (new), `engine/options/equity_commodity.py`
(new), `engine/options/portfolio.py` (new), plus edits to `pricer.py` /
`inputs.py` / `store.py`. Full detail in `engine/options/__init__.py`'s scope
ledger (source of truth, kept in sync) -- this file is the "gotchas that
weren't obvious going in" version.

**OIS currency coverage is the real constraint, not G10-ness.** Real rates
only work for currencies with an OIS convention in
`engine/rates/conventions.py::CCY_RFR` -- USD/EUR/GBP/JPY/CHF/CAD/AUD, SEVEN
currencies, not the full 45-pair G10 set `options_calc.fx.g10` covers. SEK
and NOK (and NZD) have NO OIS convention anywhere in this codebase. This
directly broke the Phase 2-6 test fixture, which used EURSEK throughout: once
`inputs.py` stopped falling back to the illustrative `rate_curves` table,
every EURSEK trade started skipping "no curve SEK" unconditionally. Fixed by
switching the whole test file's default pair from EURSEK to EURUSD (both
currencies covered) -- see `tests/test_options_pricing.py`'s own top
docstring note. **If a future phase adds a new default test pair, check
`CCY_RFR` first** -- a G10-recognized pair (per `options_calc.fx.g10`) is not
automatically a real-rates-capable pair.

**The vol fixture (`data/bloomberg/fixtures/fx_vol_snapshot_v1.json`)
conveniently already has EURUSD smile data** (ATM/RR25/BF25/RR10/BF10 per
tenor, alongside EURSEK's and USDJPY's) -- no new fixture needed for the pair
switch, just different numeric assertions per pair (EURUSD's RR25 is
NEGATIVE at every tenor, opposite sign from EURSEK's -- flips which side of
the smile is richer in any test asserting a direction).

**Business252 year fraction is close to, not a small perturbation of, plain
Act/365** -- both land in a similar range (252/365 roughly tracks the
business-day fraction of a year) but genuinely differ, more so across a
holiday. Don't assume `calendar_year_fraction` and `year_fraction` agree to
several decimal places even on a holiday-free short window; they're
independently-scaled day counts, not the same number with a tiny correction.

**`marks_official`'s SPOT is only official under `source='BBG_BFXFORWARD'`,
regardless of asset class** (`schema.py::OFFICIAL_MARK_SOURCE`, unconditioned
on `instrument_id`). Equity/commodity underlyings have no dedicated SPOT
source of their own yet, so `equity_commodity.py`'s underlying-SPOT read (via
`marks_official`, per the task's explicit instruction) only works if that
underlying's SPOT mark is stamped the same FX-flavored source string --
genuinely odd for an equity index, flagged to housekeeper in the ledger, not
worked around here (would mean reading raw `marks` instead of
`marks_official`, contradicting the task's instruction to use the official
view).

**New tables this phase, no CHECK constraints anywhere in `schema.py`
(verified) so no schema edit was needed to introduce new `asset_class`
('EQ_OPTION', 'CMDTY_OPTION') or `mark_type` ('DELTA_PA') string values** --
neither was in `OFFICIAL_MARK_SOURCE` at the time Phase 7 landed, so
`DELTA_PA` didn't surface via `marks_official` yet. **Update (2026-09-17,
housekeeper closed this the same day):** `data/ingest/schema.py` at HEAD
(commit 5de7272) now maps `DELTA_PA` -> `QL_OPTIONS_PRICER` in
`OFFICIAL_MARK_SOURCE`, so `DELTA_PA` IS official and visible via
`marks_official` as of that commit -- any test asserting an exact
`marks_official` mark-type set for a premium-adjusted pair (e.g. EURUSD)
must include `DELTA_PA` or assert the six Greeks as a floor rather than an
exact set (see `test_price_and_store_writes_premium_and_delta_marks_visible_via_marks_official`
in `tests/test_options_pricing.py`, fixed the same day this gotcha was
caught by a coordinator mid-task). `equity_dividend_yields` and
`vol_surface_points` are this package's own new tables (created via
`ensure_tables()`, called defensively inside every read path, same pattern as
`inputs.py::ensure_option_vols_table`) -- remember to call `ensure_tables`
(or let a public read/write function do it) before any raw `conn.execute`
against either table; a bare `conn.execute` against a table that hasn't been
created by `create_schema` (these two aren't) raises
`sqlite3.OperationalError: no such table`, not a friendly skip.
