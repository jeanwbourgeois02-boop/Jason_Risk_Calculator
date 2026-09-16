# Risk monitor: how it works

A plain-language guide for the person who will rely on the numbers.
Written 2026-09-15 against the committed code (244 tests passing).
Every statement here is taken from the code; file names are listed at the end so it can be checked.

**Read in order.** Sections 1 to 3 are the essentials. Sections 4 onwards are detail you can dip into.

---

## 1. The one-minute version

The app is a local web page (Python + Dash) that replaces the `HA-portfolio vJean.xlsx` calculator for the NMMF fund's FX forward book at BNP. Start it with `py 2_launcher.py start`; it opens at `http://127.0.0.1:8050`. One PC, no server, no login.

It does three things:

1. **Loads the BNP daily report** into a local database, checking every row.
2. **Shows the forward book as a cash ladder**: which currency settles when, and what it is worth in USD.
3. **Computes P&L**, and keeps a daily history so Daily / 5-day / MTD / YTD can be shown.

Three things you must know before quoting a number from it:

- **It only calculates FX forwards (and shows cash balances).** Of the rest of the FX book: a spot trade would load as a forward with a T+2 value date, FX swaps are loaded as two separate forwards and never paired, and futures have a position but no trades, so no P&L. Rates and options are not built. See section 6.
- **It shows three different P&L figures, and they will not agree.** Each is labelled. Section 3 explains which is which. Deciding which one is "the" number is your call, not the app's.
- **Nothing is invented.** If a rate is missing, the figure reads "Unavailable" or blank. It never shows zero or an estimate in place of a missing input. On a PC without Bloomberg, every USD figure is therefore blank.

---

## 2. Where the data comes from

### The BNP report is the only source of trades

- File `HA_PNL_YYYYMMDD.csv` (or the same layout in Excel). A file dated `T` shows the **close of T−1**.
- Only `Fund = NMMF` rows of type FORWARD, CURRENCY and FUTURES are loaded. Rate swaps are skipped.
- Each forward becomes two legs: the base currency at BNP's `Quantity`, the quote currency at minus `Local Cost`. Every trade keeps its own fill. Nothing is averaged.
- Every row is checked against BNP's own arithmetic (quantity × rate = cost, MV identities, P&L identities). A row that fails, or a description the parser cannot read, **stops the whole import**. Nothing partial is written.
- Re-uploading the same file is safe: identical rows are skipped. A row that differs from what is already stored (an amended fill, say) is reported as a conflict and the import is refused rather than overwriting.
- The app suggests the snapshot date from the file name. Correct it after a holiday; the app does not know holidays.

### The Excel workbook is a formula reference only

Uploading it opens a read-only view of its cells and formulas. It does not add trades and cannot supply rates (the saved copy has Bloomberg errors in every live cell).

### Rates come from Bloomberg, tagged by source

| Source tag | What it is | Feeds P&L? |
|---|---|---|
| `BBG_BFXFORWARD` | Live spot and forward outrights at an exact Bloomberg tenor | **Yes, the official source** |
| `BBG_INTERP` | Forward outright interpolated between two tenors | No, never |
| `BNP_BVAL` | BNP's own `Price` and `Fx` columns | No, reconciliation only |
| `WORKBOOK_REFERENCE` | Rates typed by hand in the "Workbook FX rates" panel | Only for the workbook method, when you select it |

Section 5 covers the Bloomberg feed in detail.

---

## 3. One P&L figure, not three

As of 2026-09-15 (`docs/BUILD_PLAN.md`) the app moved to a single headline valuation. The old "three P&L figures" (exposure cards, workbook mark-to-market, P&L ledger) are gone from the main flow; the workbook method is kept only as a side-by-side reconciliation check, described in 3.4.

### 3.1 The blotter is the one list

Every trade ever done, open or settled, is one row (`engine.pnl.valuation.value_book`). Each row is valued at:

- **FX spot/forward**: the outright for that trade's own value date, not one shared maturity for every trade. `pnl_local = quantity × (outright − fill)`, converted to USD at **today's spot** (never at the outright).
- **Futures**: `contracts × multiplier × (settlement price − fill)`.
- A missing mark makes that row's P&L "Unavailable" with a reason attached to the row — never zero, never guessed.
- Once a leg's value date has passed, the trade is frozen (realised) at the spot observed on its value date and never recalculated again.

### 3.2 LTD and the four periods

- **LTD** (life-to-date) = the sum of every row's P&L, today. It only moves when a mark moves or a trade is added — settling a trade does not move it, because the settled row is frozen at the value it already had.
- **Daily / 5d / MTD / YTD** = LTD today minus LTD on a reference day (previous business day, 5 business days back, last business day of the previous month/year). Nothing is added up day by day; each period is one subtraction using the trading calendar (`config/holidays.txt`).
- **Trading** = P&L of only the trades booked today.
- Any of these can say "Unavailable" — with a reason — if a mark needed for that day's LTD is missing.

### 3.3 Delta and stress (the ladder side)

Delta is currency exposure, not P&L: open legs summed by currency at spot, plus open futures' USD delta. From that, a 1 % move per currency and named stress scenarios (`config/stress.yaml`, e.g. "EM −10 %") are computed — simple multiplication, no correlation, no volatility.

### 3.4 The workbook reconciliation (Reconciliation tab only)

The Excel's `All FX trades` formulas, reproduced literally, kept only so today's numbers can be checked against BNP and the spreadsheet side by side. It is known to be financially wrong in the ways documented in `docs/excel-parity-audit.md` (one shared `WORKDAY(today,5)` outright for every trade regardless of real value date, dividing by the mark instead of converting at spot, T−2 dividing by the T−1 mark, and so on) and none of it feeds the header or the blotter.

### 3.5 What none of this does

No discounting, no carry or roll-down attribution, no FIFO lot matching, no fees or cash interest, no VaR, no P&L by trader (only one strategy exists in the data). IRS and options are not yet in the blotter (`docs/BUILD_PLAN.md` section 7).

---

## 4. The screens

**Above the tabs** is the data-source strip (unchanged): it names the BNP snapshot every tab is using and holds the **Upload BNP report** button; after a successful import the strip and every tab's as-of date move to the new date.

**The header**, shown above the tabs on every screen, is the one place the headline numbers live: LTD, Daily, 5d, MTD, YTD and Trading (3.2), the as-of date, the last mark time and feed status, and a collapsible LTD line chart. Picking a date on the Ladder tab's date picker moves the header to that date too.

Four tabs are shown, in this order:

### Ladder — "What am I long/short and when is it cash?"

A date-by-currency grid of local delta, spot and USD delta at the as-of date, including cash balances and NDF notionals; Net and Gross USD; an open-futures USD delta line; the stress block (3.3) below it. No P&L appears on this tab.

### Blotter — "Where did the P&L come from?"

Every row of `value_book` at the as-of date, with filters (open/settled, product, pair, strategy, theme, date range), an optional group-by summary showing LTD/Daily/MTD/YTD per group, spot/carry columns, and a row expand showing the legs and marks used to price that trade. FX swaps show as one expandable row for the pair.

### Market data — "Can I trust the numbers?"

Every mark the book needs today, with its value, source and time, and a status of official / interpolated / manual / missing; the close-completeness strip over past days; the manual-entry form; the pull button, feed status and Bloomberg diagnostics (moved here from the old ladder toolbar).

### Reconciliation — "Do I agree with BNP and the Excel?"

Two independent checks, side by side, neither feeding the header: our numbers against BNP's per instrument (with a break column), and the literal workbook method (3.4) with its manual rates grid.

---

## 5. The Bloomberg feed

Starts automatically with `py 2_launcher.py start` when `blpapi` is installed and a Terminal answers on port 8194. Set up once with `py 2_launcher.py setup` (installs `blpapi` when it detects a Terminal); check with `py 2_launcher.py doctor --bloomberg`.

Every 2 minutes, for every pair with an open leg:

- **Spot**: live `PX_LAST`, stored as official. This is **live mid at pull time**, not the 15:00 New York snapshot the Excel uses. There is no end-of-day close process yet.
- **Forwards**: the pair's `FWD_CURVE` table, then one value per open settlement date and for `WORKDAY(today, 5)`:
  - date matches a tenor exactly: stored official;
  - date between two tenors: interpolated, **not official**;
  - date beyond the last tenor: FAILED.

A rate older than 10 minutes is flagged STALE but still used.

Two warnings before trusting a forward mark:

1. Almost every real value date is a broken date, so most forward marks will be **interpolated and therefore not official**. In Official mode the workbook panel will be mostly blank until you either type rates in or decide to accept interpolated marks as official.
2. The `FWD_CURVE` column parsing was written from documentation, **never against a real Terminal**. The first live run may need a fix; the diagnostic prints the raw columns if parsing fails.

---

## 6. What is not covered

| Product | Status |
|---|---|
| FX forwards (deliverable and NDF) | Fully handled |
| FX cash balances | Shown in the ladder, no P&L |
| FX spot trades | Would load as a forward with a T+2 value date; none in the data |
| FX swaps | Each leg loads as a separate forward. Totals are unaffected, but the app cannot tell a swap from two outrights, and a near leg that has settled leaves the far leg looking like an outright. The pairing rule in CLAUDE.md is **not implemented**; the planned blotter is where swaps should be identified |
| Equity futures (ESU6) | BNP position row is loaded, but BNP gives no fills and the Excel blotter is not imported, so no futures P&L |
| Interest rate swaps | Skipped at import |
| FX options | Not built |
| Crosses (EURSEK) | Would load but are excluded from the ladder (no USD leg); none in the data |

Net USD exposure therefore excludes futures, options, rates and cash.

---

## 7. Where things stand today (2026-09-15)

- The database holds one snapshot date, 2026-08-17, with 229 forwards across 18 pairs. **The book is a month stale** until a newer BNP file is uploaded. Trades that have settled since are treated as settled.
- **No machine this app has run on has had Bloomberg.** The database holds zero official marks. Every P&L card on the development PC reads Unavailable. The app has not yet produced a live number.
- The database is `data/raw/risk.db`. It is **not in git**. Back it up: it holds the trades, marks and the snapshot history behind Daily / MTD / YTD.
- **A fresh computer needs nothing copied across.** On launch the app creates an empty database and the Bloomberg status file if they are missing. Upload a BNP report to fill it. Copy `risk.db` from another PC only if you want that PC's history. `py 2_launcher.py setup` does the same creation by hand.
- **Sample data is in git.** `py 2_launcher.py setup --sample` imports `data/sample/HA_PNL_SAMPLE_20260818.csv`: the real BNP layout with 35 forwards over 18 pairs, cash rows and the futures row, amounts scaled and ids replaced. Rates are real, positions are not. Use it to see the screens working before the first real file.

---

## 8. Day-to-day routine

1. Press **Upload BNP report** at the top, choose the morning file, confirm the date, press **Import**. Read the result line.
2. On the Bloomberg PC, check the ladder shows `BLOOMBERG LIVE`. Open the diagnostics panel once to see which forwards are exact, interpolated or failed.
3. **Set the as-of date to today.** The feed stamps marks with today's date, so a ladder left on an old date finds no official marks.
4. For the workbook method, either type the outrights into the Workbook FX rates panel, or switch the source to Official and accept blanks on broken dates.
5. Daily / 5d / MTD / YTD history is backfilled automatically in the background whenever a Terminal is available (on `pnl` / `start`, and again after every live feed cycle); nothing to run by hand.

---

## 9. Straight answers to likely questions

**Is this P&L the same as BNP's?**
No, by design. BNP marks each forward at its own value date and converts at its own spot. The exposure method marks at spot only; the workbook method uses one shared date with a divisor. BNP's marks are stored and can be selected for the workbook panel, but never drive the headline. There is no per-trade app-vs-BNP reconciliation report yet.

**Is it the same as the Excel?**
The formulas are copied literally and the 26 saved futures cells match. No FX P&L value was ever saved in the Excel, so that has not been demonstrated. Compare the two when the Excel next recalculates on a Bloomberg PC, before retiring it.

**Which number do I quote?**
Decide once. Exposure is simple and transparent; workbook is what the desk knows but is wrong in known ways. Quoting both without naming the reference will cause confusion.

**Where is the forward curve in the P&L?**
Nowhere in the exposure method. In the workbook method, only through the single `WORKDAY(+5)` outright. Neither values a forward at its own value date. The data model already stores a mark per settlement date, so this can be built.

**What happens at settlement?**
Ledger: frozen at spot on the value date and moved to Realised. Ladder: the leg drops out once as-of passes the value date. Workbook panel: kept and marked forever, as in the Excel.

**Can I trust Daily P&L tomorrow?**
Only if today's snapshot was stored complete and the feed runs again tomorrow. The card names the snapshot it compared against, or the reason it could not.

**Could a number be silently wrong?**
The rule is that a missing input shows blank, never zero. The remaining risks are: the unconfirmed NDF list; the weekday-only calendar; the untested `FWD_CURVE` parsing; an old as-of date left on the picker; and the workbook method's own arithmetic, wrong by design and labelled.

**How do I know which version is running?**
The console prints a fingerprint at launch. `py 2_launcher.py doctor` says whether the folder matches GitHub and whether a running copy is on older code. The launcher refuses to reuse a running instance with different code.

---

## 10. Decisions that would change the numbers

Full list in `docs/open-questions.md`. The ones that matter most:

1. Which P&L method is the reference.
2. Whether interpolated forward marks may count as official.
3. Confirm the NDF list with BNP; decide on fixing-rate realisation.
4. Choose the official mark time (live, 15:00 NY or close) and add an end-of-day snapshot.
5. Holiday calendar (NY, London or both).
6. Get futures fills, from the Excel blotter or from BNP.
7. Build a per-trade app-vs-BNP reconciliation report.

---

## 11. Where the code is

```
2_launcher.py                  setup / start / doctor (see README, and 1_setup.cmd / pnl)
3_diagnostic.py                root entry point for the in-app Bloomberg diagnostics (thin wrapper)
ui/launch.py                   what `start` runs: port choice, stale-instance check, browser
tools/bbg_diagnostics.py       canonical diagnostics implementation (session, marks coverage, curves...)
tools/bloomberg_terminal_probe.py  standalone probe; what `doctor --bloomberg` runs: every request OK/FAILED, writes reports/
tools/make_sample_data.py      rebuild the sample from the real file (real file never in git)
data/raw/risk.db               the database (not in git; back it up)
data/ingest/bnp.py             BNP parser and reconciliation checks
data/bloomberg/live.py         2-minute feed, status file
data/bloomberg/fwd_curve.py    FWD_CURVE parsing and interpolation
data/bloomberg/backfill.py     ledger history from daily closes (run automatically; see start_auto_backfill)
engine/ladder/exposure.py      exposure P&L (3.1)
engine/pnl/pnl.py              workbook formulas (3.2)
engine/pnl/aggregate.py        workbook daily / trading, weekday calendar
engine/pnl/ledger.py           realised, snapshots, periods (3.3)
ui/tabs/cash_ladder.py         Cash ladder tab
ui/tabs/pnl.py                 Overall book tab
docs/excel-parity-audit.md     cell-by-cell audit of the workbook
docs/open-questions.md         every unresolved assumption
tests/                         244 tests: arithmetic and fixtures, not live-data parity
```
