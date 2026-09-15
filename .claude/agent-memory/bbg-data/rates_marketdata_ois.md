---
name: rates_marketdata_ois
description: data/bloomberg/rates_marketdata.py (OIS curve layer ported from the Rates Swap Calculator reference project) -- fake-blpapi element-access gotcha, scope, curve_quotes table ownership
metadata:
  type: project
---

`data/bloomberg/rates_marketdata.py` ports the reference project
`C:\Users\jeanw\OneDrive\Documenti\Rates Swap Calculator\swapcalc\marketdata\{base,bloomberg,tickers,filesource}.py`,
OIS-only (USD SOFR, EUR ESTR, GBP SONIA, JPY TONA, CHF SARON, CAD CORRA, AUD AONIA).
Term-rate/basis/XCCY curves from the reference project were deliberately not ported.

**Fake-blpapi element-access gotcha (important if touched again or ported elsewhere):**
The reference project's `bloomberg.py` `_to_plain_dicts`/`_element_to_python` walks response
elements generically via `el.isArray()` / `el.numElements()` / `el.name()`. The fake `blpapi`
module built into `tests/test_bloomberg.py` (`_install_fake_blpapi`, shared with
`pull_marks.py` tests) does **not** implement `isArray()`/`numElements()`/`name()` on its
`FakeStructElement` -- only `hasElement(name)`, `getElement(name)`, `getElementAsString(name)`,
`numValues()`, `getValueAsElement(i)`, `getValue()`. Using the reference project's generic
walk against this fake silently breaks (AttributeError). `data/bloomberg/pull_marks.py`
already solved this by reading fields by explicit name (`fd.hasElement(f)` /
`fd.getElement(f).getValue()`) rather than enumerating unknown fields -- `rates_marketdata.py`
follows the same pattern (`_fetch_reference` / `_fetch_historical_single` /
`_fetch_historical_series`), not the reference project's `_send`/`_to_plain_dicts`. Bulk
fields (e.g. `CURVE_TENOR_RATES` for `get_bbg_curve`) are read via
`fd.getElement("CURVE_TENOR_RATES").getValue()` which the fake returns as the raw Python list
set by the test responder -- no special bulk-array handling needed against this harness.
Any future port from that reference project into this repo should use the same by-name
access style, not the generic element walk, or extend `_install_fake_blpapi` first.

**Session**: `RatesBloombergSource` opens its own `blpapi.Session`, separate from
`pull_marks.py`'s. Left un-merged deliberately; flagged to housekeeper as a follow-up for
`docs/open-questions.md` (bbg-data does not own `docs/`, so this was reported, not fixed).

**curve_quotes table**: owned by `data/ingest/schema.py` (data-ingest agent), being added there
in a parallel task. `rates_marketdata.write_curve_quotes()` / `ensure_curve_quotes_table()`
create it defensively with `CREATE TABLE IF NOT EXISTS` matching the task-specified columns
(`as_of_date, ccy, index, tenor, ticker, value, quote_type, field, source`, PK on
`as_of_date+ccy+index+tenor+source`) so the module works whether or not that migration has
landed yet. Uses `INSERT OR REPLACE` so re-running a pull for the same day/source updates
rather than raising a duplicate-key error. Note `index` is a SQL reserved word -- always
quoted (`"index"`) in DDL/DML.

**Test fixture**: no `v1_snapshot.json` existed in the reference project (checked, absent) --
built an equivalent OIS-only fixture at `data/bloomberg/fixtures/ois_snapshot_v1.json`
(schema: `as_of`, `source`, `curves` keyed by CCY, `fixings` keyed by CCY, `bbg_curves` keyed
by CCY). `RatesFileSource` mirrors the reference project's `FileSource` pattern, currency-keyed
(no `CCY/INDEX` composite key since this phase is one index per currency), case-insensitive
currency lookup.

**Ticker map**: hardcoded as a Python dict (`OIS_CURVES`) in the module itself rather than a
new yaml config file -- avoids adding a yaml/pyyaml dependency for a 7-currency, OIS-only
scope. Most non-USD tickers are tagged `# UNVERIFIED`, carried over unchanged from
`tickers.yaml` in the reference project (nothing in this repo has verified them against a
live terminal either).
