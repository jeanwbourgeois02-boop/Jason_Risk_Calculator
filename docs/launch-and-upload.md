# Launch and upload

Start the app by typing `pnl` in a PowerShell terminal (installed by `1_setup.cmd`), or
`py 2_launcher.py start` in the project folder. The browser opens at
`http://127.0.0.1:8050`. Keep the terminal open; Ctrl+C stops the app. Running `pnl`
again while the app is running just reopens the browser tab.

## Upload

The only input is the transaction-level trade blotter export (one row per fill; the
reference file is `data/raw/new_sample_trades.csv`). Use **Upload trade file** at the
top right of every tab:

1. Choose the export (`.csv`, `.xlsx` or `.xls`). Comma, semicolon, tab or pipe
   delimited; UTF-8, BOM or cp1252; header on any row; any header casing; multi-sheet
   workbooks with real Excel date cells; day-first (`20/8/2026`) or month-first
   (`8/20/2026`) dates, detected per file.
2. The file name and a **Confirm insert** button appear once the file looks like a
   blotter (it has `Symbol`, `Trade Id` and `Fin Type` or `Product` columns). Nothing is
   written before Confirm.
3. Click **Confirm insert**. One line reports what was loaded: trades (new / updated),
   by product (forwards, futures, options, rate swaps), legs, and anything excluded
   (other funds, cancelled status, earlier versions of a repeated trade id) or skipped
   because a row could not be read, with the row number and the reason.

Re-uploading the same or a newer export is safe: a trade id already on file is
replaced, everything else is added, and FX swap packaging re-runs. Option terms typed
in the Blotter's Options view (strike, payoff type, barrier) are never overwritten by a
re-upload.

A row is skipped only when its status is cancelled / rejected / pending / void, when
its fund is populated and is not NMMF, or when two populated fields contradict each
other. A blank or oddly formatted field never rejects a row when the value can be
recovered from another column.

## After the upload

- **Ladder** defaults to today (New York). It shows delta by currency and value date,
  Net / Gross USD, the futures line and the stress block. It needs official Bloomberg
  spot marks for today; on a PC without a Terminal the rate cells say so.
- **Blotter** shows every trade with its P&L (Total book, FX, Futures, Rates, Options,
  Bundles). Options whose export carried no strike are listed at the top of the
  Options view's **Option terms** editor; enter the strike and payoff type once.
- **Market data** shows every mark the book needs today, its source and freshness,
  the **Pull now** button, the manual-entry form, and **Check Bloomberg connection**,
  which runs the same diagnostics as `py 3_diagnostic.py`.

Details of what the numbers mean: [HOW_IT_WORKS.md](HOW_IT_WORKS.md).
