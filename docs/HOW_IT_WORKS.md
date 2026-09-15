# Risk monitor: how it works

A plain-language guide for the person who will rely on the numbers.
Written 2026-09-15 against the committed code (243 tests passing).
Every statement here is taken from the code; file names are listed at the end so it can be checked.

**Read in order.** Sections 1 to 3 are the essentials. Sections 4 onwards are detail you can dip into.

---

## 1. The one-minute version

The app is a local web page (Python + Dash) that replaces the `HA-portfolio vJean.xlsx` calculator for the NMMF fund's FX forward book at BNP. Start it with `launch.bat`; it opens at `http://127.0.0.1:8050`. One PC, no server, no login.

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

## 3. The three P&L figures

This is the section to understand. The app shows three P&L calculations side by side. Each is labelled on screen. They use different arithmetic and give different numbers.

### 3.1 Exposure P&L (the headline cards and the ladder)

Each open forward is valued at **today's spot**, ignoring forward points.

```
P&L = local amount × spot (USD per unit of local) − USD amount at entry
```

- Simple, transparent, matches the reference screenshot exactly.
- Not a fair-value mark: a six-month forward in a high-carry currency (TRY, BRL, MXN) shows P&L that is partly just unaccrued forward points.
- Sign follows BNP: sold AUD shows as negative AUD.

### 3.2 Workbook mark-to-market (collapsed panel on the ladder, and the Overall book tab)

Reproduces the Excel's `All FX trades` formulas **literally**, on your explicit instruction. It is known to be financially wrong in several ways, and the app copies those on purpose so it matches the Excel.

```
if pair ends in USD:   P&L = C × (G − E) / E
otherwise:             P&L = C × (G − E) / G
```

where `C` is the signed USD amount of the trade, `E` the fill and `G` the outright.

The deliberate quirks it copies from the Excel:

- One outright at `WORKDAY(today, 5)` is used for **every** trade, whatever its real value date.
- P&L is converted by dividing by the mark, not at spot. About 2.3 % error on TRY.
- T−2 P&L divides by the T−1 mark.
- USDBRL yesterday's rate is set equal to today's.
- Settled trades are never dropped; they keep being marked.
- 5-day, MTD, YTD, Net and Gross show "Unavailable": the Excel has no formula for them, or depends on hand-typed cells that are not loaded.

Parity with the Excel has only been proven on the 26 futures cells the Excel saved (all match to 0.0). The Excel saved no FX P&L values, so FX parity cannot be shown until the workbook is recalculated on a Bloomberg PC. Details in `docs/excel-parity-audit.md`.

### 3.3 The P&L ledger (Realised / Unrealised / Daily / 5d / MTD / YTD cards)

Built on the exposure method, plus settlement and history:

- **Unrealised** = exposure P&L on open trades (3.1).
- **Realised**: once a trade's value date passes, it is frozen at the official spot dated its value date. If no spot exists on or before that date the trade is listed as "not realisable" and left out, never guessed. NDFs are realised at spot on the value date, **not the official fixing**, so this will differ from BNP.
- **Total LTD** = realised + unrealised, flagged *complete* only when every currency had a rate and every settled trade could be realised.
- **Daily / 5d / MTD / YTD** = today's total minus the *complete* snapshot on the reference day. One snapshot is stored per day each time the feed runs. If the reference snapshot is missing or incomplete the card says so.
- History can be **backfilled**. Run `backfill_history.bat` on the Bloomberg PC: it pulls the daily close of every pair the book has traded, from the last business day of the previous year to yesterday, and writes one snapshot per day. Days already complete are skipped. Trades opened and settled between two BNP uploads are missing from that history, so it is only as complete as the file archive. Without a backfill, history begins the first day the feed runs.
- The calendar is Monday to Friday only. Around a US or UK holiday the reference day is off by one, and the note says so.

### 3.4 What none of them do

No discounting, no carry or roll-down attribution, no FIFO lot matching, no fees or cash interest, no VaR or stress, no P&L by trader or strategy (only one strategy exists in the data).

---

## 4. The screens

**Above the tabs** is the data-source strip. It names the BNP snapshot every tab is using (date, trade count, position count) and holds the **Upload BNP report** button. Choosing a file shows its name, the suggested snapshot date and an **Import** button. The app recognises the HA-portfolio workbook by its sheets and treats it as preview-only; there is nothing to select. After a successful import the strip and both as-of pickers move to the new date.

Two of the six tabs have content: **Cash ladder** and **Overall book**. FX, Rates, Options and Delta are placeholders that show row counts only.

### Cash ladder tab (refreshes every 2 minutes)

From top to bottom:

1. **Toolbar**: as-of date (defaults to the latest BNP date), currency order, feed status, "Pull from Bloomberg now", and the valuation source for the workbook panel.
2. **Four cards**: Net USD exposure, Gross USD exposure, Exposure P&L, plus a status card. Net = sum of USD deltas; Gross = sum of their absolute values. If any currency lacks a spot, all three say Unavailable and name the currency.
3. **The ladder**: rows are settlement dates, columns are currencies, cells are the signed local amount settling that day. Six summary rows underneath: FX rate, local delta, USD delta, USD delta at entry, exposure P&L, settlement type. The right-hand column is each date's USD value at spot, undiscounted.
   - NDF currencies (BRL, TWD, KRW, IDR) stay in the ladder flagged NDF. They are exposure notionals, not cash flows. **This NDF list has not been confirmed with BNP.**
   - Trades are "open" when trade date ≤ as-of ≤ value date. A leg settling on the as-of date is in the ladder but carries no delta.
4. **Workbook mark-to-market** (collapsed): the method in 3.2, trade by trade, with the workbook rate, divisor, and the difference from the actual broker leg.
5. **P&L ledger cards** (3.3), with collapsed tables of realised trades and snapshot history.
6. **Bloomberg diagnostics** (collapsed, opens itself on failure): every requested mark as OK / FAILED / SKIPPED with Bloomberg's error text.
7. **Workbook FX rates** (below the tabs). The grid takes General spot, Current, T−1 and T−2 outright per pair, all at the shared `WORKDAY(date, 5)` maturity, and saves them as `WORKBOOK_REFERENCE` marks.

### Overall book tab

The workbook method (3.2) aggregated by pair, with book totals and the period table. Defaults to the "Workbook rates" source, so it is blank until rates are typed in or the source is switched to Official.

---

## 5. The Bloomberg feed

Starts automatically with `launch.bat` when `blpapi` is installed and a Terminal answers on port 8194. Set up once with `setup_bloomberg.bat`; check with `run_bloomberg_diagnostic.bat`.

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

---

## 8. Day-to-day routine

1. Press **Upload BNP report** at the top, choose the morning file, confirm the date, press **Import**. Read the result line.
2. On the Bloomberg PC, check the ladder shows `BLOOMBERG LIVE`. Open the diagnostics panel once to see which forwards are exact, interpolated or failed.
3. **Set the as-of date to today.** The feed stamps marks with today's date, so a ladder left on an old date finds no official marks.
4. For the workbook method, either type the outrights into the Workbook FX rates panel, or switch the source to Official and accept blanks on broken dates.
5. **Once, after the first successful live run**, run `backfill_history.bat` so Daily / 5d / MTD / YTD have a history to compare against.

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
The console prints a fingerprint at launch. `check_git_push.bat` says whether the folder matches GitHub. The launcher refuses to reuse a running instance with different code.

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
launch.bat                     start the app
setup_bloomberg.bat            one-time install on the Bloomberg PC
run_bloomberg_diagnostic.bat   read-only Bloomberg check, writes reports/
backfill_history.bat           rebuild Daily/5d/MTD/YTD history from Bloomberg closes
check_git_push.bat             is this folder in sync with GitHub
data/raw/risk.db               the database (not in git; back it up)
data/ingest/bnp.py             BNP parser and reconciliation checks
data/bloomberg/live.py         2-minute feed, status file
data/bloomberg/fwd_curve.py    FWD_CURVE parsing and interpolation
data/bloomberg/backfill.py     ledger history from daily closes (backfill_history.bat)
engine/ladder/exposure.py      exposure P&L (3.1)
engine/pnl/pnl.py              workbook formulas (3.2)
engine/pnl/aggregate.py        workbook daily / trading, weekday calendar
engine/pnl/ledger.py           realised, snapshots, periods (3.3)
ui/tabs/cash_ladder.py         Cash ladder tab
ui/tabs/pnl.py                 Overall book tab
docs/excel-parity-audit.md     cell-by-cell audit of the workbook
docs/open-questions.md         every unresolved assumption
tests/                         243 tests: arithmetic and fixtures, not live-data parity
```
