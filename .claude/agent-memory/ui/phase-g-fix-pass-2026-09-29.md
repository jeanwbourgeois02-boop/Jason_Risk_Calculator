---
name: phase-g-fix-pass-2026-09-29
description: Phase G fix pass (checkers A/B/C + speed): price-to-check flags on the Book, one valuation per date wired, warm-up of every P&L period / Book panel / Risk subset, Data rows kept server-side, narrow-width CSS, the See-fills mount trap
metadata:
  type: project
---

2026-09-29, fix pass after checkers A, B and C (one ui agent). What is true after it:

- **Price to check (C1):** `book.price_checks` reads `data_checks.mark_records`, the Data tab's memoised marks-check rows, which are shared. `book.gathered` = `gather` + `with_checks` (a shallow copy: the shared gather is never edited). A CHECK row's `blocks` mark each fill; the trade gets one amber "price to check" flag per flagged price. Now, Today, Daily and LTD show a grey ≈, and Daily and LTD lose their green / red. The total row adds "≈ N to check". The leg's Mark cell turns amber with a ≈. Render and CSV read `gathered`, never `gather`.
- **One valuation per date (S6):** `daily_series(..., value_fn=raw_value_book)` in pnl, the header chart and warmup: all three must pass the same function, since the engine keeps one memo per function. `curve_positions(..., value_fn=raw_value_book)` in `shared_curve`. `book_positions(conn, as_of, value_fn=, curve=shared_curve, spreads=shared_spreads)` in risk_folds, header and blotter.
- **Warm-up adds:** book price_checks; `risk_subsets` (the unfiltered subset_var and each Type / Commodity group); every P&L period but Custom; `book.history` of each open trade; `market_data.warm` (the body with `diag=False`). `risk.subset` is memoised per set of names.
- **Data payload:** the marks store holds a token {as_of, n, at}. `_marks` and its CSV read the rows from `mark_records` (`_token_rows`). The Diagnostics body is built by its own callback on the Summary's clicks (`DIAG_SUMMARY_ID`), never on a tab switch. About 170 kB down to 79 kB.
- **Trap fixed:** the Blotter's See-fills callback had Input(FILLS_STORE_ID), a store in the app layout, with `initial_duplicate`. It fired on every page load with its outputs missing, which put a console ReferenceError on every load. It now fires on Input(FILLS_BODY_ID, "id") when the Blotter mounts, with the store as State.
- **Narrow widths:** at 1400 px and below, `.book-main` table slots stay `overflow: visible` so the heads and the total row stay sticky. What it is clips (`.tk-clip`), and the coverage sits beside it in `.tk-cov`. Small markers wrap under their figure. The Book fits at 1100 px.
- **Type filter:** its values are `tf.type_key` (`NOT_TYPED` for a trade of only unrecognised contracts), labelled by `key_label`, the same as the rows.

**How to apply:** time clicks with `scratchpad/fix/perf.py` (fetch-wrapped) and `run_perf.sh <label> base|new sample|real`. The base code is `git archive <commit>` run through `FIX_REPO`. Measured after the pass: every click on the warm sample and on Jason's file is under 0.5 s, except P&L By month (0.58 s: the month cells filter the frame for every cell) and, on Jason's file, the Risk commodity row / unit switch (0.7-1.1 s, 72 kB: every fold is re-rendered). See [[browser-timing-recipe-2026-09-29]].
