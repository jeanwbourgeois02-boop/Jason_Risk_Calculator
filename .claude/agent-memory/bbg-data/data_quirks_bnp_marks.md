---
name: data-quirks-bnp-marks
description: Quirks of deriving marks from the BNP PB CSV (data/bloomberg/bnp_marks.py) discovered on HA_PNL_20260818.csv
metadata:
  type: project
---

Facts observed extracting BNP_BVAL marks from `data/raw/HA_PNL_20260818.csv` (as_of 2026-08-17):

- 229 FORWARD rows collapse to 19 distinct (pair, value_date) keys for FWD_OUTRIGHT and
  18 distinct pairs total. **The reference file has no cross pairs** (neither leg USD on
  both sides) — every pair has USD on one side: 4 are XXXUSD (AUDUSD, EURUSD, GBPUSD,
  XAUUSD), the other 14 are USDXXX (USDBRL, USDCAD, USDCHF, USDHKD, USDIDR, USDJPY,
  USDKRW, USDMXN, USDNOK, USDSEK, USDSGD, USDTRY, USDTWD, USDZAR).
- Across the real file, every group of rows sharing (pair, value_date) has **identical**
  `Price` and `Fx` — zero reject/conflict rows on the reference file. The
  conflict-detection path (`extract_bnp_marks` rejecting two rows with different Price
  for the same key) is therefore only exercised by a synthetic CSV in
  `tests/test_bloomberg.py::test_bnp_marks_conflicting_rows_are_rejected`, not the real
  file.
- `Fx` is quote_ccy -> USD, **not** a general "spot" column. For a USDXXX row this gives
  a genuine XXX->USD rate, so SPOT = 1/Fx is derivable (e.g. USDTRY SPOT from Fx =
  0.020877 (TRY->USD, 6dp) gives spot ~= 47.8996 via 1/Fx — matches CLAUDE.md's worked
  example). For an **XXXUSD row, Fx is always exactly 1.0** (USD->USD) by construction —
  it is not a base spot and must never be used as one; `bnp_marks.py` skips SPOT
  entirely for the 4 XXXUSD pairs in the real file (logged as a warning), while still
  emitting their FWD_OUTRIGHT. A prior version of this code wrongly set `spot = fx` for
  XXXUSD rows, silently emitting SPOT = 1.0 for all 4 pairs — fixed; regression test is
  `test_bnp_marks_no_spot_is_1_0_and_xxxusd_pairs_have_no_spot`.
- Cross pairs (neither leg USD, e.g. EURSEK — not present in the reference file): `Fx`
  alone only gives quote_ccy->USD, so the pair SPOT requires a *second* row elsewhere in
  the file whose quote_ccy is the cross's base_ccy (never an XXXUSD row, whose Fx is
  always 1.0) to get base_ccy->USD. `bnp_marks.py` builds this map from every FORWARD
  row's own (quote_ccy, Fx) before emitting any marks, so the base rate can come from a
  totally different pair's row. If it's genuinely absent, the SPOT for that cross is
  skipped with a warning; the FWD_OUTRIGHT is still emitted (never blocked by a missing
  SPOT).
- `load_bnp_marks` inserts through the same shared validation as `load_marks_csv`
  (`marks_csv.load_mark_rows`), not a direct `executemany`: re-running it on the same
  file/as_of produces duplicate-key rejects (strict=False) or a ValueError (strict=True)
  instead of a raw sqlite IntegrityError, and an unknown instrument_id is a reject
  rather than a foreign-key failure.
