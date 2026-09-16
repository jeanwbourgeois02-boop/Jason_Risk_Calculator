---
name: blotter-bnp-reconcile
description: engine/pnl/reconcile.py design -- blotter (XLSX source) vs BNP EOD netting check, tolerance choice
metadata:
  type: project
---

- 2026-09-16: built `engine/pnl/reconcile.py::reconcile_blotter_vs_bnp(conn, as_of_date,
  abs_tolerance_usd=1.0, rel_tolerance=0.5e-6)` per user instruction, as a new small
  check -- explicitly NOT a resurrection of the retired `engine/pnl/pnl.py` workbook
  arithmetic or the retired `ui/tabs/reconciliation.py`. It compares net USD notional per
  `instrument_id` between `trades.source = 'XLSX'` (the real-time blotter,
  `data/ingest/blotter.py`) and `trades.source = 'BNP'` (the once-daily EOD-Hong-Kong PB
  snapshot, `data/ingest/bnp.py`) -- two independent sources for the same macro trader's
  book that should agree end of day.

- Netting reuses the "Display notional" USD-leg lookup already established in
  `engine/pnl/aggregate.py::aggregate_by_pair` and documented in [[data_quirks]]: query
  `trade_legs WHERE ccy = 'USD'` per trade_id rather than assume a leg position (USD leg
  is leg 1 for USDXXX, leg 2 for XXXUSD). Both `trades` sources store individual fills
  with their own real `trade_date` (BNP's is parsed from the row description's "TD"
  date, not the file's snapshot date), so filtering both sides on
  `trade_date <= as_of_date` is sufficient -- no arithmetic against the BNP file's T-1
  snapshot date is needed inside the query itself.

- Tolerance: CLAUDE.md gives per-row identity tolerances (e.g. Local Cost rounds to
  whole quote units) but no number for a pair-level *netted* comparison. Chose
  `max($1.00, 0.5e-6 * larger side)`, reusing the MV Local -> MV Base identity's $1
  floor / 0.5e-6 relative scale by analogy, since summing N trades per side can
  accumulate up to ~N quote units of BNP rounding residue and a flat cent-level
  tolerance would false-flag a clean book with many small trades. This is a judgment
  call, not a contract number -- revisit if a real reconciliation run shows it's too
  tight or too loose.

- A pair with no USD leg at all (a genuine cross like EURSEK) nets to 0 on that side by
  design; this check only nets each pair's own USD leg (same limitation as the xlsx
  display convention it mirrors). The 2026-08-18 reference file has no crosses (see
  [[data_quirks]]), so this hasn't been exercised against a real cross yet.
