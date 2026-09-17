---
name: vol_marketdata_fx_vol_feed
description: data/bloomberg/vol_marketdata.py (FX option vol feed, options_calc merge Phase 5) -- schema, ticker convention, read helpers, gotchas
metadata:
  type: project
---

Built 2026-09-17 as options_calc merge Phase 5, mirroring [[rates_marketdata_ois]]'s
two-source structure (VolBloombergSource / VolFileSource behind get_vol_quotes).

**Schema**: `vol_quotes(as_of_date, pair, tenor, quote_type, value, ticker, field, source,
snapped_at)`, PK `(as_of_date, pair, tenor, quote_type, source)`. `quote_type` in
{ATM, RR25, BF25, RR10, BF10}. `value` is stored RAW in vol points (7.85 = 7.85%) --
unlike rates_marketdata's OIS quotes, which ARE pre-scaled to decimal via
`scale_quote`. The consumer (options-pricer) must divide by 100 itself; this module
never does that scaling.

**Ticker convention (ALL UNVERIFIED -- 9 numbered probe items in the module
docstring)**: built by function `vol_ticker(pair, tenor, quote_type)`, not a
per-pair table (unlike OIS_CURVES) since one formula covers every pair:
`<PAIR><infix><TENOR> BGN Curncy` where infix = V/25R/25B/10R/10B. Tenors
`ON,1W,2W,1M,2M,3M,6M,9M,1Y` -- 'ON' is itself an unverified guess (probe item 7);
own `tenor_to_days` (not rates_marketdata's) handles it as 1 day since
rates_marketdata's OIS tenor set has no ON tenor.

**Read helpers for options-pricer**: `vol_smile(conn, as_of, pair)` ->
`{tenor: {quote_type: value}}`, single-source only (prefer BBG_BDP, else BBG_BDH,
else alphabetically-first -- same rule as `engine/rates/store.py::_read_curve_quotes`,
never blends sources). `atm_vol_for_expiry(conn, as_of, pair, expiry_date)` linearly
interpolates ATM in variance-time (`var = vol^2 * days_via_tenor_to_days`) between the
two bracketing tenor nodes; returns None outside the tenor range (refuses to
extrapolate) or with <2 ATM nodes staged. Delta-smile construction (RR/BF -> full
smile, feeding the vendored FXDeltaVolSurface) is deliberately NOT in this module --
that's options-pricer's job reading the raw smile back out.

**`vol_pairs_needed(conn)`**: distinct `base_ccy||quote_ccy` for
`instruments.asset_class='FX_OPTION'`; defaults to EURUSD/EURSEK/USDJPY when the book
has none yet (or the instruments table doesn't exist at all -- catches
sqlite3.OperationalError too, not just an empty result).

**CLI gotcha found while testing**: in `--probe` mode with `VolFileSource`, the
`tenors` param passed to `get_vol_quotes` is NOT honored by the file source (a
snapshot already contains whichever tenors it has), so the probe's per-quote-type
value lookup must filter `pv.quotes` by `tenor` explicitly, not just `quote_type` --
otherwise quote_type keys collide across all 9 tenors in the snapshot and you silently
get e.g. the 1Y value when probing 1M. Fixed once; worth remembering if this pattern
is copied elsewhere.

**Fixture**: `data/bloomberg/fixtures/fx_vol_snapshot_v1.json`, as_of 2026-09-17,
EURUSD/EURSEK/USDJPY x all 9 tenors x all 5 quote_types, clearly labelled
`"source": "test-fixture-synthetic"`.
