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
