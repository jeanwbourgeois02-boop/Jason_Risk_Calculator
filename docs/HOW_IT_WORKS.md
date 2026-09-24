# Risk monitor: how it works

A plain-language guide for the person who will rely on the numbers.
Rewritten 2026-09-17 against the committed code (711 tests). Every statement here is taken from the code; file names are listed at the end so it can be checked.

**Read in order.** Sections 1 to 3 are the essentials. Sections 4 onwards are detail you can dip into.

---

## 1. The one-minute version

The app is a local web page (Python + Dash) that replaced the `HA-portfolio vJean.xlsx` calculator for the NMMF fund. Start it with `py 2_launcher.py start` (or the `chelsea` shortcut); it opens at `http://127.0.0.1:8050`. One PC, no server, no login.

It does three things:

1. **Loads the trade blotter export** (one row per fill: FX forwards and spot, FX swaps, ES futures, FX options, interest rate swaps) into a local database.
2. **Values every trade, every day, one way**, and shows the result as one blotter with one LTD figure and the Daily / 5d / MTD / YTD differences.
3. **Shows the book as a delta ladder**: which currency you are long or short, when it settles, and what a move does to it.

Three things to know before quoting a number from it:

- **Nothing is invented.** A missing market rate makes the figure read "Unavailable" with the reason next to it. It never shows zero or an estimate in place of a missing input.
- **It has not yet produced a live number.** No machine it has run on has had a Bloomberg Terminal. Every figure shown so far comes from the labelled fallback (the old BNP file's rates from 2026-08-17) or reads Unavailable. Section 7.
- **One convention for all products** (section 3): each trade marked at the market rate for its own date, quote-currency P&L converted at spot, settled trades frozen.

---

## 2. Where the data comes from

### The blotter export is the only source of trades

- Press **Upload trade file** at the top and choose the export (`.csv`, `.xlsx` or `.xls`; the sample layout is `data/raw/new_sample_trades.csv`). Every fill is one row, with its own trade id and fill price.
- Rows are kept when their status is live and their fund is NMMF (or blank). Cancelled, rejected, pending and void rows are skipped and counted.
- The import is deliberately tolerant: encoding, delimiter, header casing, date shapes and a preamble above the header are all accepted; a field missing from one column is recovered from another (the description, the buy/sell currency columns, the currency pair). Only a genuine contradiction between two populated fields rejects a row, and one bad row never blocks the file.
- Re-uploading is safe: a trade id already loaded is replaced by the newer version.
- **Conventions fixed at import.** FX: quantity is the signed base-currency amount, positive = bought base. Futures: quantity is contracts, positive = long. Options: quantity is the notional in the pair's base currency, positive = long; the fill price is the premium as a fraction of that notional, paid in the base currency (checked against the export's own invoice amounts). Interest rate swaps: **a positive notional means you pay fixed**, negative means you receive fixed (user-confirmed 2026-09-17).
- **FX swaps.** Two forwards on the same pair, same day, opposite direction, same amount within 0.01 %, different value dates are grouped as one swap package. For a cross with no USD leg (EURSEK) the comparison is on the base amount. A pair with the same value date is an intraday round trip and stays two outrights. Ambiguous cases go to a review list, never guessed.

### The old BNP report and the Excel are history

Neither is an input any more. The BNP daily report was the trade source until 2026-09-16 and is no longer uploadable. The Excel's formulas were removed from the code on 2026-09-17 (user decision: no BNP fallback, the old workbook and everything linked to it deleted); see docs/bnp-excel-removal.md.

### Rates come from Bloomberg, tagged by source

| Source tag | What it is | Feeds P&L? |
|---|---|---|
| `BBG_BFXFORWARD` | Live spot and forward outrights at an exact Bloomberg tenor | **Yes, official for FX** |
| `BBG_BDH` | Futures settlement prices, overnight fixings | **Yes, official for futures** |
| `QL_PRICER` | Swap PV, DV01, settled cashflows from the app's own OIS curves | **Yes, official for swaps** |
| `QL_OPTIONS_PRICER` | Option premium and Greeks from the app's own pricer | **Yes, official for options** |
| `BBG_INTERP` | Forward outright interpolated between two tenors | No, never |
| `BNP_BVAL` | The old BNP file's `Price` and `Fx` columns | No; a labelled fallback for display only |
| `MANUAL` | Typed on the Market data tab | No, unless you explicitly select it |

Exactly one source is official per kind of mark. Every valuation reads only official marks.

---

## 3. One valuation, one line

### 3.1 The blotter is the one list

Every trade ever done, open or settled, is one row, valued at the as-of date's marks:

- **FX spot/forward**: `quantity × (outright for the trade's own value date − fill)` in the quote currency, converted to USD at **that day's spot**, never at the outright. A cross (EURSEK) is converted through the SEK spot; no USD leg is invented.
- **FX swap**: the near and far legs are two rows, each valued as a forward, grouped for display.
- **Futures**: `contracts × 50 × (settlement price − fill)`.
- **Interest rate swap**: present value plus the net coupons already paid, both from the app's own OIS curve (SOFR, ESTR, SONIA, TONA, SARON, CORRA, AONIA). A swap dealt at market is worth zero at the fill, so this is the same "mark minus fill" idea. Non-USD swaps are converted at spot.
- **FX option**: `quantity × (premium mark − premium fill)`, converted at spot. The premium mark comes from the app's pricer using Bloomberg spot, curves and the vol smile.
- A missing mark makes that row's P&L Unavailable with the reason on the row. Never zero.
- Once a trade's value date, expiry or maturity has passed it is **frozen** at the rate observed that day and never recalculated. If no rate exists for that day, the trade is "unrealisable" and every total that includes it reads Unavailable until a rate is backfilled.

### 3.2 LTD and the periods

- **LTD** = the sum of every row. It moves only when marks move or a trade is added; settlement does not move it.
- **Daily / 5d / MTD / YTD** = LTD today minus LTD on the reference business day (previous day, 5 days back, last day of the previous month, last day of the previous year), using the holiday calendar in `config/holidays.txt`. Nothing is summed day by day. The reference-day LTD is valued at that day's historical marks, pulled from Bloomberg on demand and cached.
- **Trading** = P&L of the trades booked today.
- Each figure names its reference date, or the reason it is Unavailable.

### 3.3 Delta, the ladder and stress

Delta is exposure, not P&L: every open leg summed by currency, marked at spot, one column per currency and one row per settlement date. It includes forwards, swaps, NDFs (flagged non-cash), and since 2026-09-17 option deltas (base-currency delta = notional × the pricer's delta, quote side the opposite at spot). Open futures are a separate USD line. From the per-currency USD delta the tab shows a 1 % move and the named scenarios in `config/stress.yaml` (edit that file to change them). No correlations, no vol.

### 3.4 What it does not do

No discounting of FX P&L, no FIFO lot matching, no fees or cash interest, no VaR, no P&L by trader. The ladder is a delta table: it does not show NDF settlement amounts at fixing, swap coupon dates, option premium cash or bank cash balances (the BNP balance feed was removed with the BNP upload). Options are realised at their last premium mark, not at intrinsic value on expiry day. Equity and commodity options are priced by the library but have no trade feed yet.

---

## 4. The screens

**Top bar**: the three tabs and the **Upload trade file** button.

**The header**, under the tab bar on every screen, is the one place the headline numbers live: LTD, Daily, 5d, MTD, YTD and Trading, then Net USD delta (with "long USD" / "short USD" spelled out) and Gross USD delta, and a collapsible LTD line chart. The Ladder tab's date picker moves the header too. A note on the right says how many rows are priced from the BNP fallback rather than Bloomberg, when any are.

### Ladder — "What am I long/short and when is it cash?"

Defaults to today. One headline Delta card (gross, with net and direction underneath), then the currency-by-date grid with local delta, spot and USD delta rows, the open-futures block, and the risk-and-scenarios table (per currency, plus futures). No P&L on this tab. Trades that could not be placed (an option with no delta mark, say) are counted as "Unresolved" in the caption, never dropped.

### Blotter — "Where did the P&L come from?"

Six sub-tabs. **Total book**: every row, a P&L strip for the rows in view, and a P&L-by-asset-class table (FX, Futures, Rates, Options, Total) that always sums to the strip. **FX** and **Futures**: the same rows scoped by product, with the marks used per trade. **Rates**: the swap blotter with PV, DV01, settled cashflows and P&L. **Options**: a grouped, collapsible risk grid (portfolio totals, asset class, structure, leg) with market value, Greeks, expiry, strike and forward. **Bundles**: named groups of pairs with their own LTD / Daily / MTD / YTD. Filters on every table narrow both the rows and the strip.

### Market data — "Can I trust the numbers?"

By currency pair: spot with its source and time, the forward curve for that pair with one row per settlement date the book needs, a chart, the close-completeness strip for the last 20 business days, a manual-entry form, and the **Pull now** and **Check Bloomberg connection** buttons with the feed status line.

---

## 5. The Bloomberg feed

Starts automatically with `py 2_launcher.py start` when `blpapi` is installed and a Terminal answers on port 8194. Set up once with `py 2_launcher.py setup`; check with `py 2_launcher.py doctor --bloomberg`.

Every 2 minutes, as of today:

- **Spot** per pair with an open leg, live `PX_LAST`, stored official.
- **Forwards**: one outright per open settlement date from the pair's `FWD_CURVE` table. Exact tenor: official. Between tenors: interpolated, **not official**. Beyond the last tenor: failed.
- **Futures** settlement per open contract.
- **Swaps**: OIS curve quotes and overnight fixings per currency, then every swap repriced.
- **Options**: vol smile per pair, then every option repriced (premium and Greeks).
- Then trades whose date has passed are frozen (section 3.1).

History for Daily / 5d / MTD / YTD is backfilled in the background from daily closes at 17:00 New York whenever a Terminal is available.

Two warnings: most real value dates are broken dates, so many forward marks will be interpolated and not official until you decide otherwise; and the `FWD_CURVE` parsing and every vol ticker were written from documentation, never against a real Terminal, so the first live run may need a fix. The diagnostic prints the raw responses.

---

## 6. What is covered

| Product | Status |
|---|---|
| FX forwards, spot, NDFs, crosses | Valued, realised, on the ladder |
| FX swaps | Packaged from the blotter; each leg valued as a forward |
| ES futures | Valued from per-fill prices; USD delta on the ladder |
| Interest rate swaps | Valued from the app's OIS curves; PV, DV01, cashflows; not on the ladder |
| FX options (vanilla, digital, barrier, touch, Asian, American) | Valued and Greeks from the app's pricer; delta on the ladder; grouped view in the Blotter |
| Swaptions, caps/floors | Pricing library present; no trade feed yet |
| Equity and commodity options | Pricing library present; no trade feed yet |
| Cash balances | No source since the BNP upload was removed |

---

## 7. Where things stand today (2026-09-17)

- **Never run live.** Zero official marks in the database; every valuation is Unavailable or on the labelled BNP fallback dated 2026-08-17. The first run on the Bloomberg PC is the next step (section 8).
- **The database on this PC is stale.** It was loaded before the option parser recorded strikes, and still carries the retired BNP trades and positions. Move it aside and re-upload the blotter before the first live run.
- The database is `data/raw/risk.db`. It is not in git. Back it up: it holds the trades, the mark history behind Daily / MTD / YTD and the frozen settlements.
- A fresh computer needs nothing copied across: launch creates an empty database; upload a blotter to fill it. `py 2_launcher.py setup --sample` loads the sample blotter in `data/sample/blotter_sample.csv`.

---

## 8. Day-to-day routine

1. **Upload trade file**: choose the export, read the result line (rows loaded, skipped, rejected and why). If the Options view lists options with no strike on file (digitals), enter their terms once in its **Option terms** editor.
2. On the Bloomberg PC, open Market data and check the feed status line reads connected. Press **Pull now** if you do not want to wait two minutes.
3. Leave the Ladder on today. The feed stamps marks with today's date; a ladder left on an old date finds no official marks.
4. Read the header. If a figure is Unavailable, the reason names the pair and date; Market data shows which mark is missing.
5. History for the period figures backfills itself; nothing to run by hand.

---

## 9. Straight answers to likely questions

**Is this P&L the same as BNP's?**
Not exactly, by design: BNP prints its `Price` to 5 decimals and uses its own spot. The BNP rates are kept as a labelled fallback, never as the official number.

**Is it the same as the Excel?**
No. The Excel marked every pair at one date five days out, divided by the mark instead of converting at spot, and mis-stated futures. Those formulas were removed from the code on 2026-09-17; only git history keeps them.

**What happens at settlement?**
Blotter: the row is frozen at the rate observed on its date and stays in LTD forever. Ladder: the leg drops out once the as-of date passes its settlement date.

**Can I trust Daily P&L tomorrow?**
Only if today's marks and yesterday's are both on file. The card names the reference date, or the reason it could not.

**Could a number be silently wrong?**
A missing input shows blank, never zero. The remaining risks are: the unconfirmed NDF list (BRL, TWD, KRW, IDR); the untested Bloomberg field parsing; vol tickers unverified; an old as-of date left on the Ladder picker; and the fallback caption, which you must read before quoting a figure on a PC without Bloomberg.

**How do I know which version is running?**
The console prints a fingerprint at launch. `py 2_launcher.py doctor` says whether the folder matches GitHub and whether a running copy is on older code.

---

## 10. Decisions that would change the numbers

Full list in `docs/open-questions.md`. The ones that matter most:

1. Whether interpolated forward marks may count as official.
2. Confirm the NDF list with the prime broker; realise NDFs at the fixing.
3. Whether the ladder should also show cash flows (NDF settlement, swap coupons, option premium) and where a cash balance would come from.
4. Realising options at intrinsic value on expiry day.

---

## 11. Where the code is

```
2_launcher.py                  setup / start / doctor
ui/launch.py                   what `start` runs: port choice, stale-instance check, browser
tools/bloomberg_terminal_probe.py  what `doctor --bloomberg` runs
data/raw/risk.db               the database (not in git; back it up)
data/ingest/blotter.py         blotter parser (the only trade source)
data/ingest/swaps.py           FX swap packaging rule
data/bloomberg/live.py         2-minute feed, status file
data/bloomberg/backfill.py     mark history from daily closes
engine/pnl/valuation.py        one row per trade, all products (3.1)
engine/pnl/ledger.py           realisation, LTD, periods (3.2)
engine/pnl/stress.py           1 % move and scenarios (3.3)
engine/ladder/exposure.py      the delta ladder; exposure_adapter.py feeds it legs and option deltas
engine/rates/                  OIS curves and swap valuation (QuantLib)
engine/options/                option pricer and Greeks (vendored options_calc, QuantLib)
ui/tabs/                       header, cash_ladder, blotter (+ blotter_fx, rates, options, blotter_bundles), market_data
config/holidays.txt            business-day calendar; config/stress.yaml scenarios
docs/BUILD_PLAN.md             the specification
docs/open-questions.md         every unresolved assumption
tests/                         711 tests: arithmetic and fixtures, not live-data parity
```
