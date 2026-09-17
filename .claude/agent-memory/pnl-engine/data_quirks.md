---
name: data-quirks
description: Data/contract quirks discovered while building engine/pnl (spot derivation, real-file coverage, schema access patterns)
metadata:
  type: project
---

- The 2026-08-18 BNP reference file (`data/raw/HA_PNL_20260818.csv`) has 229 FORWARD/NMMF
  rows and every pair is either USDXXX or XXXUSD -- there are no cross pairs (verified by
  listing `Symbol[:6]` uniques: AUDUSD, EURUSD, GBPUSD, USDBRL, USDCAD, USDCHF, USDHKD,
  USDIDR, USDJPY, USDKRW, USDMXN, USDNOK, USDSEK, USDSGD, USDTRY, USDTWD, USDZAR, XAUUSD).
  Consequently every trade in this file has a derivable quote->USD spot: XXXUSD trades
  trivially (S=1.0, quote is USD) and USDXXX trades because `bnp_marks.extract_bnp_marks`
  always emits a SPOT mark for the USDXXX row itself. **The "no derivable spot" count on
  this file is 0** -- don't assume the count is nonzero without checking; a test that
  asserts "there are cross pairs without spot" against this file would be vacuously wrong.

- Quote-currency -> USD conversion for any pair (not just crosses) reduces to one rule:
  if quote_ccy == 'USD', S = 1.0; otherwise look up the SPOT mark of the **'USD'+quote_ccy**
  instrument (settle_date = as_of_date) and take S = 1 / that pair rate. This is exactly
  the same lookup whether the trade's own pair is USDXXX (in which case 'USD'+quote_ccy
  IS the trade's own instrument) or a genuine cross like EURSEK (in which case
  'USD'+quote_ccy = 'USDSEK', a different instrument's mark, not EURSEK's own SPOT).
  `bnp_marks.extract_bnp_marks` stores a cross's SPOT under the cross's own instrument_id
  (e.g. 'EURSEK'), which is *not* what the P&L quote->USD conversion needs -- don't reuse
  that value for USD conversion of a cross; it only reconciles the cross's own rate.

- `marks_official` is a SQLite VIEW (`data/ingest/schema.py`), so it can be queried with
  a plain `SELECT ... FROM marks_official WHERE ...` exactly like a table; no special
  handling needed from pandas' `read_sql_query`.

- `trades` has no `settle_date` column; for FX_FWD it must be joined from `trade_legs`
  (leg_no=1 or 2 carry the same settle_date = value_date for spot/forward trades). Both
  legs of an FX forward share one settle_date, so `JOIN trade_legs l ON l.trade_id =
  t.trade_id AND l.leg_no = 1` is sufficient to get it without a GROUP BY.

- A repo-wide grep test enforcing "never divide by a variable named mark/m/spot" also
  matches plain English inside docstrings/f-strings (e.g. the phrase "mark/spot" or "/
  mark" in a comment sentence) if the test only strips `#`-comments, since Python
  docstrings aren't comments. When writing code under such a grep constraint, avoid the
  literal substrings "/ mark", "/ m", "/ spot" anywhere in prose too, not just in real
  divisions -- reworded CLAUDE.md must-not-replicate references in engine/pnl/pnl.py's
  docstring and error message to route around this.

- tests/test_bloomberg.py has 13 pre-existing failures (`NameError: name 'NY' is not
  defined` in `data/bloomberg/pull_marks.py:277`, `record_environment`), unrelated to
  engine/pnl. Confirmed via `git stash` that they fail identically on a clean checkout
  before any pnl-engine changes -- this is bbg-data's bug to fix, not pnl-engine's.
  UPDATE (2026-09-14, suite 135 passed): bbg-data has since fixed this upstream --
  test_bloomberg.py is now fully green on a clean checkout. Don't assume this failure
  still exists; re-check before citing it.

- `_TRADE_SQL` in `engine/pnl/pnl.py` must filter both `l.settle_date > :as_of` AND
  `t.trade_date <= :as_of`. Filtering only on settle_date lets `ltd_per_trade(conn,
  ref_date, strict=False)` (used by `aggregate.period_pnl` for daily/5d/mtd/ytd reference
  dates) pick up trades dated *after* ref_date whenever a mark happens to exist for that
  ref_date -- turning "daily P&L" into `m_t - m_ref` instead of `m_t - f` for same-day
  trades (reviewer-caught, C-1, 2026-09-14: a same-day USDJPY trade produced daily =
  13,652.04 vs ltd = 6,802.72 because LTD(t-1bd) wrongly included it). CLAUDE.md: "Trading
  P&L = LTD of trades with trade_date = t" implies LTD(ref) must exclude any trade dated
  after ref by construction, not rely on missing marks to accidentally NaN it out. Covered
  by `test_daily_pnl_excludes_trades_dated_after_reference_date` in tests/test_pnl.py --
  that test's pattern (an old, flat-marked trade to keep both LTD sums non-NaN, so
  `result["daily"] == result["ltd"]` can be asserted directly) is reusable for similar
  reference-date regressions.

- For `trade_legs`, "the USD leg" of an FX trade is whichever of the two legs has
  `ccy = 'USD'` -- for a USDXXX pair that's **leg 1** (the base leg, `amount = quantity`
  itself, e.g. USDJPY's leg1 ccy is USD with amount = trades.quantity), not leg 2 (the
  quote leg, `amount = -quantity * price`, which is JPY for USDJPY). For an XXXUSD pair
  (e.g. AUDUSD) it's the reverse: leg 2 is the USD leg (`amount = -quantity * price`).
  When computing a display USD notional as `sign(quantity) * |USD-leg amount|` (the xlsx
  convention, CLAUDE.md "Display notional"), don't assume the USD leg is always leg 2 --
  query `trade_legs WHERE ccy = 'USD'` rather than hard-coding leg_no per pair type.

- pandas `read_sql_query` with a plain sqlite3 connection cannot mix `?` (qmark) and
  `:name` (named) placeholders in the same query -- sqlite3's DB-API picks one style
  from the whole statement based on the *first* placeholder it sees, so a query using
  `IN (?,?,?)` for a tuple of products must also use `?` for every other parameter
  (e.g. `AND t.trade_date <= ?`), not `:as_of`, or pandas raises
  `DatabaseError: ... Binding N (':as_of') is a named parameter, but you supplied a
  sequence`. `engine/pnl/pnl.py`'s existing queries avoid this by using named params
  throughout and building the `IN (...)` list from a separate `.format()`'d literal
  count of `?`s passed positionally at the *start* of the params tuple -- either be
  fully positional or fully named in one query, never mixed.

- 2026-09-17: added `engine/pnl/xlsx_fx_replica.py` (user-authorised, one-off override
  of the "Must not replicate" list) -- a literal replica of the old xlsx workbook's "All
  FX trades" row-per-fill formula, but sourced from live `trades`/`trade_legs`, not the
  xlsx file. Reuses `pnl.py`'s `workbook_fx_pnl`/`workbook_valuation_date` unchanged
  rather than re-deriving the formula/quirks. Produces t-1/EOD/t-2 mark+P&L columns per
  trade; t-2's P&L deliberately divides by the t-1 mark (bug item 2), and futures always
  divide by the mark not fill (bug item 1) since a futures instrument_id never ends in
  "USD". Missing marks on any of the three observation dates surface as Python `None`
  in that trade's row, never 0 or NaN-silently-summed. This module is separate from and
  must never be confused with `engine/pnl/pnl.py`'s Reconciliation-tab arithmetic --
  both replicate the same workbook formula but from different data sources (this one
  from our own trades tables, pnl.py historically tied to the xlsx file itself) and
  CLAUDE.md's override note only applies per-table, not blanket.
