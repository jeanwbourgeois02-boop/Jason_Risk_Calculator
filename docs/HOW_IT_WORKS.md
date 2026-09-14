# Risk monitor: what it does, how every number is made, and what it does not do yet

Written 2026-09-15 against the code as committed (`0cd44ac`, test suite 239 passed).
This is the plain-language reference for the person who will rely on the numbers.
It is deliberately blunt about limits. Every calculation below is quoted from the code
that produces it, with the file named so it can be checked.

---

## 1. What the app is

A local web application (Python + Dash) that replaces the `HA-portfolio vJean.xlsx`
calculator for the NMMF fund's FX book at BNP.

It does three things today:

1. **Imports the BNP position/P&L report** (`HA_PNL_YYYYMMDD.csv` or the same layout in
   Excel) into a local SQLite database, with reconciliation checks.
2. **Shows the FX forward book as a settlement-date cash ladder** by currency, with
   live USD translation when a Bloomberg Terminal is on the machine.
3. **Computes P&L two different ways** (explained in section 5) and keeps a daily
   snapshot history so Daily / 5-day / MTD / YTD can be shown once history exists.

It runs on one PC, no server, no login. Start it with `launch.bat`; it opens in the
browser at `http://127.0.0.1:8050`.

### What it covers today, honestly

| Product | Status |
|---|---|
| FX forwards (deliverable and NDF) from the BNP file | **Fully handled**: trades, legs, ladder, both P&L methods |
| FX cash balances from the BNP file | Ingested and shown in the cash ladder; no P&L |
| FX spot trades, FX swaps | Code paths exist but **no such trade has ever been in the data**; the swap-pairing rule is untested on real swaps |
| Equity futures (ESU6) | BNP position row is ingested; **no futures trades are created** (BNP gives no fills). The xlsx blotter that holds the fills is **not imported**. Futures P&L therefore does not appear |
| Interest rate swaps | **Skipped at import.** Nothing shown |
| FX options | **Not built.** Nothing shown |
| Crosses (EURSEK) | Would be ingested but excluded from the exposure ladder (no USD leg); none in the data today |

Four of the six tabs (**FX, Rates, Options, Delta**) are **empty placeholders** that only
show row counts. All real content lives on the **Cash ladder** tab and the **Overall
book** tab.

---

## 2. Inputs and where the data comes from

### 2.1 The BNP report (the only trade source)

- File dated `T` is the **T−1 close** snapshot. `HA_PNL_20260818.csv` holds trades
  through 2026-08-17. The app suggests that date on upload; you must correct it after a
  holiday, the app does not know holidays.
- Only rows with `Fund = NMMF` and `Financial Type` in FORWARD, CURRENCY, FUTURES are
  loaded. INTEREST_RATE_SWAP is logged and skipped.
- Each FORWARD row's description (`TD 08/15/2026 VD 09/16/2026 SELL USD VS .BUY AUD @ 0.70060700`)
  is parsed with a strict pattern. A row that does not match is **rejected, never
  guessed**, and the whole import is refused.
- A trade becomes two legs: base currency amount = BNP `Quantity`, quote currency
  amount = −`Local Cost`. Every trade keeps its own fill rate. No averaging.
- Re-uploading the same file is safe: identical rows are skipped. A row that differs
  from what is stored (an amended fill, an amended cash balance) is reported as a
  **conflict and the import is refused**; nothing is overwritten.
- Reconciliation checks run on every import (`data/ingest/bnp.py`):
  `|Quantity × rate − Local Cost| ≤ 1`, `|Quantity × (Price − rate) − MV Local| ≤ 0.05`,
  MV Base vs MV Local × Fx within rounding, and the BNP P&L identities
  (DTD = MV − start-of-day MV, MTD = MV − prior month-end MV).

**Current database contents** (2026-09-15): 229 FX forwards, 18 FX pairs, one
snapshot date (2026-08-17), 31 netted position rows. **The book is one month stale**:
every trade done after 2026-08-17 is absent until a newer BNP file is uploaded. Trades
that settled between then and now (first settlement 2026-09-08) are still in the
database and are treated as "settled" by the logic below.

### 2.2 The HA-portfolio workbook

Used as the **formula specification** only. Uploading it in the app opens a read-only
preview of its cells and formulas. It does **not** import its trades, it does not
recalculate, and it cannot supply rates: the saved copy contains Bloomberg errors
(`#N/A Terminated`, `#VALUE!`) in every live FX cell.

### 2.3 Market data

There are three sources of rates in the database, kept strictly apart by a `source`
tag. **Only one is "official"** and feeds P&L by default:

| Source tag | What it is | Official? |
|---|---|---|
| `BBG_BFXFORWARD` | Live spot (`PX_LAST`) and forward outrights read at an **exact** Bloomberg tenor | **Yes** |
| `BBG_INTERP` | Forward outright **linearly interpolated** between two Bloomberg tenors (or between spot and the first tenor) | No, never |
| `BNP_BVAL` | The `Price` and `Fx` columns of the BNP file (BNP's own marks) | No, reconciliation only |
| `WORKBOOK_REFERENCE` | Rates **typed by hand** in the "Workbook FX rates" panel | Only when you pick "Workbook rates" in the dropdown |

**As of today, the database holds zero official marks.** It holds 33 BNP marks and no
workbook-entered rates. Bloomberg has never been connected on any machine this app has
run on; the last diagnostic (`reports/bloomberg_diagnostic_20260914_155219.txt`) shows
`blpapi` not installed and port 8194 refused. Consequently, on the development PC,
**every P&L card reads "Unavailable"** and every USD column is blank. This is by design
(nothing is invented) but it means the app has not yet produced a single live number.

---

## 3. The Bloomberg feed (when it exists)

`data/bloomberg/live.py`, started automatically by `launch.bat` if `blpapi` imports and
a Terminal answers on `localhost:8194`.

Every 2 minutes, for every FX pair with an open leg:

- **Spot**: live `PX_LAST` on `<PAIR> Curncy`. Stored as `SPOT`, official, stamped with
  the wall-clock time. Note: this is **live mid at pull time, not the 15:00 New York
  snapshot** the workbook uses for its historical columns.
- **Forward outrights**: one request per pair for the bulk `FWD_CURVE` table
  (`FWD_CURVE_QUOTE_FORMAT = OUTRIGHTS`), then for each open settlement date and for
  the workbook's shared date `WORKDAY(today, 5)`:
  - date equals a tenor row exactly: stored official;
  - date between two tenors: interpolated, stored `BBG_INTERP`, **not official**;
  - date beyond the last tenor: no value, reported FAILED.

**Two consequences worth knowing before trusting a forward mark:**

1. Broken dates (almost every real value date) will be **interpolated**, so the
   "Official" forward mark for most legs will be **absent** and the workbook
   mark-to-market will stay blank in Official mode. Workbook MTM will in practice need
   the "Workbook rates" input or a decision to accept interpolated marks as official.
2. The parsing of the `FWD_CURVE` table's column names is written from documentation
   and unit tests, **not from a real terminal response**. The first live run may need
   a fix. The diagnostic prints the raw column names when parsing fails.

The feed also writes a status file next to the database and the Cash ladder tab shows a
**Bloomberg diagnostics** panel listing every request as OK / FAILED / SKIPPED with
Bloomberg's error text. A rate older than 10 minutes is flagged STALE but still used.

Setup on the Bloomberg PC: `setup_bloomberg.bat` once, then `launch.bat`. Check with
`run_bloomberg_diagnostic.bat`.

---

## 4. The Cash ladder tab, top to bottom

Everything on this tab re-renders every 2 minutes and on every rate save.

### 4.1 Toolbar
- **As of**: the snapshot date. Defaults to the latest imported BNP date (2026-08-17).
  Trades are "open" when `trade_date ≤ as-of ≤ settle_date`.
- **Currency order**: by |USD delta| or A-Z.
- **Book / Bloomberg feed**: one-line status.
- **Pull from Bloomberg now**: one synchronous pull.
- **Workbook MTM valuation source**: Workbook rates / Official / BNP_BVAL. Affects
  only the workbook mark-to-market section (4.5), not the exposure ladder.

### 4.2 Four cards: exposure snapshot
Formulas in `engine/ladder/exposure.py`, per currency `c` over open trades:

```
Local delta[c]     = Σ signed local amounts (all settlement dates)
USD delta[c]       = Local delta[c] × spot (USD per unit of c)
USD delta entry[c] = Σ over trades of the trade's own USD leg, sign of the local leg
Exposure P&L[c]    = USD delta[c] − USD delta entry[c]

Net USD exposure   = Σ_c USD delta[c]
Gross USD exposure = Σ_c |USD delta[c]|
Exposure P&L       = Σ_c Exposure P&L[c]
```

Spot is normalised to USD-per-local with an explicit inversion flag (USDJPY 150 →
1/150; AUDUSD 0.70 → 0.70). If **any** currency lacks a spot, the three USD cards say
Unavailable and name the currency. Nothing is filled with zero.

**Sign convention** is `broker_reference`: the sign BNP reports. Sold AUD shows as
negative AUD and negative USD entry. This matches the reference screenshot; it is not
a cash-account view.

### 4.3 Metadata line and feed panel
As-of, book (`HAHY7`, the BNP strategy code; the file has no "HA" book field), count
of NDF trades, unresolved trades, sign convention, rate source, latest rate timestamp.
A red `NO BLOOMBERG FEED` badge when the feed is down.

### 4.4 The combined ladder (the screenshot layout)
Rows = settlement dates, columns = currencies, cells = signed local amounts summed
across the trades settling that day. Below the dates, six summary rows: FX rate,
Local delta, USD delta, USD delta entry, Exposure P&L, Settlement type (NDF or
Deliverable). Right-hand column = USD equivalent of each date row at spot.

- **NDF currencies (BRL, TWD, KRW, IDR) stay in the ladder** as exposure notionals,
  flagged NDF. They are not physical cash flows. **This NDF list is an assumption not
  yet confirmed with BNP.**
- The USD equivalent of a future date is **undiscounted and at spot**, not at the
  forward. It is a cash value indicator, not a valuation.
- Drill-down: every trade behind a cell is retained (`cell_trades`) but the UI does
  not yet expose a click-through. The trade detail is visible in section 4.5 instead.

### 4.5 "Workbook mark-to-market — separate calculation" (collapsed)
The workbook's own arithmetic, applied trade by trade. See section 5.2. Shows a
currency/date summary, the full trade calculation detail (entry rate, workbook
valuation rate, divisor, workbook quantity C, P&L, and the difference between the
workbook's value and the actual broker leg), and the raw cash settlement table
including cash balances.

### 4.6 P&L ledger (cards)
Realised LTD, Unrealised (open), Total LTD, Trading today, Daily, 5-day, MTD, YTD.
See section 5.3. Below it, collapsed tables of realised trades and the snapshot history.

### 4.7 Bloomberg diagnostics (collapsed, auto-opens on failure)
Every requested mark with its status, the spot rates in use, and pull warnings.

### 4.8 Upload a file / Workbook FX rates (below the tabs)
- Upload: choose file → check the snapshot date → Import. Result line says what was
  added, skipped, or why it was refused.
- Workbook FX rates: a grid per pair of General spot (optional), Current, T−1, T−2
  outright, all at the shared `WORKDAY(date, 5)` maturity. Saving writes them as
  `WORKBOOK_REFERENCE` marks for that date. "Read saved rates from Excel" loads the
  workbook's cached rates for review; with the supplied workbook it finds none usable.

---

## 5. The three P&L calculations and why there are three

This is the most important section. The app carries **two different P&L methods for
open trades plus a realised ledger**, and they will give **different numbers**. This
is deliberate, and each is labelled on screen, but the reader must know which is which.

### 5.1 Exposure P&L (cards and combined ladder)
```
P&L = local amount × spot(USD per local) − USD entry amount
```
Marks every open forward **at today's spot, ignoring the forward points to its value
date**. This is the screenshot's method. It is simple and reconciles to the reference
fixture exactly (AUD: −20,961,623 × 0.600 − (−15,000,000) = +2,423,026). It is **not a
fair-value mark**: a 6-month forward in a high-carry currency (TRY, BRL, MXN) will show
P&L that is partly just unaccrued forward points.

### 5.2 Workbook mark-to-market (section 4.5 and the Overall book tab)
Reproduces `All FX trades` columns H / I / K of the workbook literally, on the user's
explicit instruction (CLAUDE.md, 2026-09-14). Per trade, with `C` = workbook quantity,
`E` = fill, `G` = mark:

```
if pair ends in "USD":   P&L = C × (G − E) / E
else:                    P&L = C × (G − E) / G
```

- `C` is the **signed USD amount** of the trade (sign = direction of the base
  currency), not the base quantity. For futures it would be contracts × 50 × fill.
- `G` is the outright at the **shared** date `WORKDAY(as-of, 5)` for **every** pair,
  regardless of the trade's actual value date.
- Yesterday's LTD (H) uses yesterday's outright at **today's** shared date.
  T−2 LTD (K) uses the T−2 outright but **divides by the T−1 outright**.
- USDBRL: the workbook sets yesterday's rate equal to today's (cell N17 = O17). The
  app reproduces that.
- Matured trades are **never dropped**: the workbook keeps marking them forever, so
  the app does too in this section.
- Daily P&L = today's LTD − yesterday's LTD (workbook M4). Trading P&L = LTD of
  trades dated today (M5).
- **5d, MTD, YTD are shown as Unavailable** because the workbook has no formula for
  them. Net / Gross USD are Unavailable because the workbook's Portfolio totals depend
  on hand-typed option deltas on fixed rows that are not loaded.

**Known financial defects of this method, replicated on purpose:** dividing by the
mark instead of converting at spot (about 2.3 % error on TRY), one valuation date for
all tenors, the T−1 denominator on T−2, marking settled trades forever, and (for
futures) dividing by the price. The audit is in `docs/excel-parity-audit.md`. The only
saved numeric outputs in the workbook are 26 futures cells; the app reproduces all 26
to 0.0 USD. **No saved FX P&L exists in the workbook**, so full FX parity with Excel
has never been demonstrated and cannot be until the workbook is recalculated on a
Bloomberg machine.

### 5.3 The P&L ledger (`engine/pnl/ledger.py`)
Built on the exposure method (5.1), adds realisation and history:

- **Realised**: when a trade's settlement date is before the as-of date it is frozen
  once at the official spot **dated its settlement date** (or the last spot before it,
  noted). No spot on or before that date → "not realisable", listed, never guessed.
  **For NDFs this is spot on the value date, not the official fixing** (BRL PTAX, TWD,
  KRW, IDR fix two days before value). This will differ from BNP's settled P&L.
- **Unrealised**: open trades at the latest spot (= 5.1).
- **Total LTD** = realised + unrealised. Marked *complete* only if every currency had
  a rate and every settled trade was realisable.
- **Snapshot**: every feed cycle stores one row per calendar day (last write wins).
- **Daily / 5d / MTD / YTD** = today's total LTD − the complete snapshot on the
  reference business day (previous weekday / 5 weekdays back / last weekday of prior
  month / last weekday of prior year). Missing or incomplete reference → Unavailable
  with the reason. **History starts the first day the feed runs**; there is nothing
  to compare against before that, so YTD cannot exist until 2027 and MTD until next
  month.
- Calendar is **Monday–Friday only**. Around a US or UK holiday the reference date
  will be wrong by a day and will say so in the note.

### 5.4 Things none of the three methods do
No discounting, no carry attribution, no forward-point roll-down, no lot matching
(FIFO), no fees or interest on cash balances, no option or rates P&L, no VaR, no
stress, no attribution by trader or strategy (only one strategy exists in the data).

---

## 6. The Overall book tab

Shows the workbook method (5.2) aggregated: P&L by pair (signed USD notional, LTD,
trade count), book totals (all Unavailable, see 5.2), and the period table (LTD and
Daily populated when rates exist; 5d/MTD/YTD Unavailable). Source dropdown and as-of
date as on the ladder. Default source is **Workbook rates**, so it is blank until
rates are typed in or the dropdown is switched.

---

## 7. Operating it day to day

1. Upload the morning BNP file. Confirm the date. Read the result line.
2. On the Bloomberg PC, confirm `BLOOMBERG LIVE` on the ladder and open the
   diagnostics panel once to see that spots are OK and which forwards are exact,
   interpolated or failed.
3. **Set the As-of date to today** if you want the ledger, the workbook MTM in
   Official mode and the feed to line up. The feed stamps live marks with today's
   date; a ladder left on 2026-08-17 will find no official marks for that date.
4. For the workbook method, either type the three outrights per pair in the Workbook
   FX rates panel, or switch the source to Official and accept that broken dates will
   be blank.
5. The database lives at `data/raw/risk.db` and is **not in git**. Back it up; it
   holds the trade history, the marks history and the P&L snapshots. Losing it loses
   the Daily/MTD/YTD history.

---

## 8. Questions a macro PM will ask, answered straight

**What is my net USD position right now?**
The Net USD exposure card: sum of local deltas × spot over open forwards. It excludes
futures, options, rates and cash balances. It is only available when every currency
has a spot. With no Bloomberg it is Unavailable.

**Is this P&L the same as BNP's?**
No, and it is not meant to be. BNP marks each forward at its own value date with BVAL
(the `Price` column) and converts at its own spot. The app's exposure method marks at
spot only; the workbook method marks at one shared date with a divisor. BNP's numbers
are stored as `BNP_BVAL` marks and can be selected in the dropdown for the workbook
section, but they are never used for the headline. A formal reconciliation report
(app vs BNP per trade) is not built.

**Is it the same as the Excel?**
The formulas are reproduced literally and the 26 numeric cells the Excel saved
(futures) match to 0.0. The Excel saved no numeric FX P&L, so equality on FX has not
been demonstrated. When the Excel next recalculates on a Bloomberg machine, the two
should be compared before the Excel is retired.

**Which number do I quote?**
Decide once. The exposure method is simple and transparent; the workbook method is
what the desk is used to but is financially wrong in known ways. Quoting both without
saying which is the reference will cause confusion. This is a decision for you, not
the app.

**Where is the forward curve in the P&L?**
Nowhere in the exposure method. In the workbook method it enters via the single
`WORKDAY(+5)` outright. Neither values a forward at its own value date. That is a
known gap; the data model already stores the mark per settlement date, so it can be
built.

**What happens at settlement?**
Ledger: the trade is frozen at spot on its value date and moves to Realised. Ladder:
the leg drops out once as-of passes the value date. Workbook section: the trade keeps
being marked forever, as in the Excel.

**What about NDF fixings?**
Not implemented. NDFs realise at spot on the value date, not the fixing rate, and the
gross ladder shows NDF notionals flagged NDF rather than the net USD settlement.

**Can I trust Daily P&L tomorrow?**
Only if today's snapshot was stored *complete* (every currency priced, every settled
trade realised) and the feed runs again tomorrow. The card tells you which snapshot
it compared against and why if it cannot.

**Is the rate live?**
Yes if the badge says BLOOMBERG LIVE and the timestamp is under 10 minutes old; it is
live mid at pull time, refreshed every 2 minutes. It is not a 15:00 NY or 17:00 NY
close. There is no end-of-day official close snapshot process yet.

**What is the position in futures / rates / options?**
Not shown. Futures: one BNP position row is stored but no trades. Rates: skipped.
Options: not built. The book's delta from those products is not in Net USD.

**What if the BNP file has a bad row?**
The whole import is refused and the row number and reason are shown. Nothing partial
is written from the UI path.

**Could a number be silently wrong?**
The design rule is that a missing input shows blank or Unavailable, never zero or an
estimate, and every figure carries its source and date. The remaining ways to be
wrong are: the provisional NDF list; the weekday-only calendar; the unverified
`FWD_CURVE` parsing on a real terminal; a stale as-of date left on the picker; and
the workbook method's own arithmetic, which is wrong by design and labelled as such.

**How do I know what version is running?**
The console prints an app fingerprint at launch; `check_git_push.bat` reports whether
the folder matches GitHub. The launcher refuses to reuse a running instance with
different code.

---

## 9. Open decisions that change the numbers

Full list in `docs/open-questions.md`. The ones that matter most for the P&L:

1. Which P&L method is the reference (5.1 vs 5.2).
2. Whether interpolated forward marks may be treated as official.
3. Confirm the NDF list with BNP and decide on fixing-rate realisation.
4. Choose the official mark time (live, 15:00 NY, or close) and add an end-of-day
   snapshot job.
5. Holiday calendar (NY, London, or both).
6. Import the xlsx blotter for futures fills, or get fills from BNP.
7. Whether to build a per-trade app-vs-BNP reconciliation report.

---

## 10. Where things are

```
launch.bat                     start the app
setup_bloomberg.bat            one-time install on the Bloomberg PC
run_bloomberg_diagnostic.bat   read-only Bloomberg check, writes reports/
check_git_push.bat             is this folder in sync with GitHub
data/raw/risk.db               the database (not in git; back it up)
data/ingest/bnp.py             BNP parser and reconciliation checks
data/bloomberg/live.py         2-minute feed, status file
data/bloomberg/fwd_curve.py    FWD_CURVE table parsing and interpolation
engine/ladder/exposure.py      exposure P&L (5.1)
engine/pnl/pnl.py              workbook formulas (5.2)
engine/pnl/aggregate.py        workbook daily / trading, weekday calendar
engine/pnl/ledger.py           realised, snapshots, periods (5.3)
ui/tabs/cash_ladder.py         the Cash ladder tab
ui/tabs/pnl.py                 the Overall book tab
docs/excel-parity-audit.md     cell-by-cell audit of the workbook
docs/open-questions.md         every unresolved assumption
tests/                         239 tests: arithmetic and fixtures, not live-data parity
```
