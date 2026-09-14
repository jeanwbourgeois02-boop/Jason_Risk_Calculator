Start the app by typing `pnl` in a terminal, or double-click `launch.bat` in
the project folder. The browser opens automatically. Keep the terminal open;
Ctrl+C stops the app. Running `pnl` again opens the existing instance.

On this computer, the existing WindowsApps `pnl.cmd` now points to this
project. Its previous contents are saved beside it as
`pnl.cmd.risk-monitor-backup`.

Use **Upload a file** below the app tabs:

1. Choose a BNP CSV or Excel report (.xlsx, .xlsm, .xls). For Excel, select
   the worksheet containing the BNP report columns, with headers in row 1.
2. Check the snapshot date. For `HA_PNL_YYYYMMDD` filenames the suggested
   date is the previous weekday; adjust it for holidays. Other filenames
   require an explicit date.
3. Click **Import BNP report**. The cash ladder and P&L date controls update
   to the imported date. Previous snapshots remain available by date.

Identical records are skipped. Validation errors or conflicting amendments
reject the entire import; existing records are not overwritten by amendments.
The result states what was added or excluded. Existing product coverage remains:
FX forwards and cash, futures positions only, IRS excluded. BNP marks remain
reconciliation marks; this upload does not supply official Bloomberg prices.

The HA-portfolio workbook is automatically recognised as an **Excel calculation
reference**. Choose a sheet and expand **Preview worksheet / formulas**. The preview
shows the first 50 rows (up to 40 columns for XLSX/XLSM), with formulas displayed
as text. It does not recalculate Excel/Bloomberg formulas, persist the reference
workbook, or import a second copy of its trades. Other Excel layouts can also be
previewed in reference mode; only BNP-format worksheets can update report data.

Files are limited to 25 MB. Uploading does not modify the original file.

Dependencies are listed in `requirements.txt`. For a new Python installation:
`py -3 -m pip install -r requirements.txt`.


Calculation correction (2026-09-14): the cash ladder now includes currency/date
P&L totals and expandable trade-level entry/valuation rates, local amounts, general
spot reference and signed USD entry. Formula arithmetic follows `All FX trades`
in HA-portfolio vJean. See `excel-parity-audit.md` for exact formulas and caveats.

Expand **Workbook FX rates** to enter current, T-1 and T-2 outright rates, all
for the displayed shared WORKDAY(date,5) maturity. General spot is a separate,
optional field for the cash-flow USD equivalent; it does not alter the workbook
P&L formula. Saving applies to the selected date and updates both views. Rates
from the BNP report at other maturities are not silently substituted.

You can load saved rates from HA-portfolio for review. The supplied workbook has
no usable saved FX rates (Bloomberg errors), so it cannot currently populate
those cells. Its 26 surviving historical futures P&L outputs are covered by
numerical comparison tests, including database-level reconstruction for eligible
historical trades. No full current FX/Portfolio total match is claimed.

Portfolio Net/Gross remains unavailable without its manual option adjustments
and other product inputs. 5d/MTD/YTD have no formula in this workbook and remain
unavailable. The app retains recorded trades after settlement for the workbook's
LTD logic; the cash ladder shows flows from its selected date onwards. Earlier
trades already absent from the first BNP snapshot cannot be reconstructed by
uploading that snapshot alone.
