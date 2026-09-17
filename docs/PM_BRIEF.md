# Risk monitor: brief for the portfolio manager

Date: 2026-09-17. Covers the app as audited and fixed on this date (audit of every
layer: setup, import, valuation, pricers, screens, Bloomberg path, docs).

## 1. What the app is

One valuation of the NMMF book, from the trade blotter export only, marked on Bloomberg
closes, shown three ways: a P&L header on every screen (LTD, Daily, 5d, MTD, YTD,
Trading, Net and Gross USD), a Ladder (delta by currency and value date, futures line,
stress scenarios), and a Blotter (every trade with its P&L, by asset class, with
filters). A Market data screen shows which marks are on file and runs the Bloomberg
diagnostics. Setup, launch and troubleshooting are three files at the repo root
(`1_setup.cmd`, `2_launcher.py`, `3_diagnostic.py`) and one command, `pnl`.

## 2. Choices made, and why

| Choice | What it means for the numbers |
|---|---|
| **Blotter is the only trade source.** BNP's daily report and the Excel workbook were retired. | Every trade has its real fill and trade id, including futures. No netting, no re-keying. |
| **Each FX leg is marked at the outright for its own value date.** Quote-currency P&L converts to USD at spot, never at the forward. | Fixes the workbook's habit of marking every pair five days out and dividing by the mark (≈2.3 % error on TRY, BRL, MXN, IDR). |
| **Futures P&L = contracts × multiplier × (price − fill).** | The workbook understated it by fill/mark. |
| **Official close is 17:00 New York**, Bloomberg's daily FX close. | Historical and live marks agree; every mark carries its timestamp and offset. |
| **One official source per mark type.** Bloomberg BFXFORWARD for spot and outrights, BDH for futures settles, the app's own QuantLib OIS bootstrap for swap PV/DV01, the app's own option pricer for premium and Greeks. BNP prices, interpolated forwards and hand-typed marks are reconciliation only and never feed P&L. | A number is either official or shown as Unavailable with the reason. Nothing is filled with a stale or invented value. |
| **Settled trades are frozen** at the last official mark on or before settlement and never revalued. | LTD is a closed ledger; Daily / MTD / YTD are differences of the same LTD series on the trading calendar (US holidays included). |
| **Swaps: positive notional = pay fixed** (blotter convention, confirmed 2026-09-17). Swap P&L = PV + coupons already settled. | Continuous across a coupon date and at maturity. |
| **The Ladder is a pure delta table**: FX legs at spot, option delta, the open-futures USD line. No cash balances, no coupon or premium cash flows. | It answers "what am I long and when does it become cash", not "what is my bank balance". |
| **Net USD is FX only, sign + = long USD**, the same figure and sign in the header, the Ladder card and the risk table. Futures and gold are shown as their own lines. | One number, one place, one sign (fixed 2026-09-17: three different figures were on screen). |
| **Options live inside the Blotter**, MARS-style: Portfolio Totals, asset class, structure ("USDCHF - Two Leg", "EURSEK - Digital"), legs, with Position, Notional, MktVal, MktPx, Delta, Theta, Gamma, Vega, Expiry, Underlying, Strike, UndFwdPx, Rho, plus the option type and the blotter instrument id. | Greeks are the pricer's native quote-currency sensitivities converted at spot; verified against an independent Black-Scholes calculation. |
| **Option time to expiry is calendar days / 365** (market convention). | A "business-day" variant was found to shorten a one-year option by six days and was removed. |
| **Import never rejects a file over formatting.** Delimiters, encodings, header row, casing, date order (day-first or month-first, detected per file), thousands separators are all handled. Only a contradiction between two populated fields rejects a row, and then only that row. | The reference export loads 772 trades with zero rejects; a month-first copy of it loads identically. |
| **Option terms the export lacks are typed once in the app.** Three of the eight options (the digitals) carry no strike in the export. The Options view has an editor for strike, payoff type (vanilla, digital, barrier, touch, Asian, American) and barrier level; entries survive re-uploads. | Until entered, those options show "no strike on file" and are excluded from Greeks and P&L rather than priced as something they are not. |
| **Futures roots known: ES, NQ, RTY, YM** (CME equity index, third Friday, multipliers 50 / 20 / 50 / 5). Any other root is skipped with a message. | A wrong multiplier would be a wrong P&L, so nothing is guessed. |

## 3. What is in place

- FX spot, forwards, NDFs (BRL, TWD, KRW, IDR), crosses, and FX swaps packaged from
  the blotter (same pair, date, opposite amounts, different value dates; crosses on
  base amount).
- ES futures from per-fill prices.
- USD SOFR (and EUR, GBP, JPY, CHF, CAD, AUD OIS) curve bootstrap, swap PV, DV01,
  settled cashflows, overnight fixings.
- FX options: vanilla, digital, barrier, touch, Asian, American pricing with a smile
  from Bloomberg ATM / risk-reversal / butterfly quotes; premium-adjusted delta where
  the pair's convention is premium-adjusted.
- Swaption, cap/floor, equity and commodity option pricing libraries are present but
  have no trade feed yet.
- Bloomberg live feed every two minutes when a Terminal is present: spot and outrights
  per open value date, futures settles, OIS quotes and fixings, vol surfaces; option and
  swap pricing runs in the same cycle; history backfills itself.
- Diagnostics (`py 3_diagnostic.py`, and the button on Market data): session, official
  source mapping, spot / forward / futures coverage per open leg, OIS quotes, overnight
  fixings, option terms and option mark coverage, PC clock versus New York, last pull,
  and how many Bloomberg vol tickers are still unverified.
- Setup from a bare Windows PC: `1_setup.cmd` installs Python and Git if missing,
  clones or updates the repo, builds the environment, creates the database, runs the
  tests, installs `pnl`, and runs the doctor.

## 4. What is missing or still to confirm

1. **First live run on the Bloomberg PC has not happened.** Every Bloomberg request
   shape (broken-date outrights, futures settle field, vol tickers ATM / 25R / 25B /
   10R / 10B, the `ON` tenor) is a documented guess until then. Run
   `py 2_launcher.py doctor --bloomberg` and `py 3_diagnostic.py` there first; both
   name each failing request with Bloomberg's own error text.
2. **Option expiry is realised at the last premium mark, not intrinsic value.** Exact
   for an expired OTM option, approximate for an ITM one. Agreed design if wanted:
   intrinsic at spot on expiry for vanillas with a strike on file.
3. **NDF list (BRL, TWD, KRW, IDR) is assumed**, not confirmed with the prime broker,
   and NDFs are realised at spot on the value date, not at the fixing.
4. **No cash balances anywhere** (by decision). If a bank-balance ladder is wanted
   later it needs a new feed.
5. **Rates are not on the Ladder** (delta is FX only); DV01 by swap is on the Rates
   sub-tab. A non-USD swap's PV is not yet spot-converted (all current swaps are USD).
6. **Equity / commodity options, swaptions, caps and floors**: pricers exist, no
   ingest path, so they never appear until the blotter carries them.
7. **Bundles** tag trades by pair; a trade can be re-tagged individually. There is no
   grouping by strategy since the export carries no strategy column.
8. **Futures roots other than ES, NQ, RTY, YM** are skipped with a message until their
   multiplier and expiry rule are added.
9. **The delta-per-currency SQL in the contract** (CLAUDE.md) folds a future's USD
   notional at fill into the USD bucket. The screens do not use it (they use the
   mark-based futures line); it is kept as written pending a contract decision.
10. **The database on the working PC is stale** (loaded before option terms and the
    blotter-only path). Move `data/raw/risk.db` aside and re-upload the blotter before
    the first live run.

## 5. Fixed in the 2026-09-17 audit

- Settled trades could only be frozen by the Bloomberg feed, so on any PC without a
  Terminal the headline LTD went Unavailable even with every mark loaded. They now
  freeze from marks directly; the feed still records them.
- The Blotter FX view computed its t−1 / t−2 columns on a Monday-to-Friday calendar
  and disagreed with the header around US holidays.
- Three different "Net USD" figures were on screen (header, Ladder card, risk table);
  now one figure, one sign.
- The FX sub-tab's P&L strip silently included futures; it is FX only now, matching
  the Total book's FX row.
- Option time to expiry was rounded through a business-day count (six days short on a
  one-year option).
- No option could be marked as a digital; the parser now reads payoff keywords and the
  Options view has a terms editor.
- A US-locale export (month-first dates) would have loaded wrong dates silently; the
  order is now detected per file.
- The diagnostics tool failed its own official-source check forever (its table was
  stale) and defaulted to a 2026-08-17 as-of date; four checks were added (fixings,
  option terms and marks, clock, unverified vol tickers).
- The setup script called `py` and `git` in the same window that had just installed
  them and crashed; it now refreshes PATH and says exactly what to do.
- Adding a pair to a bundle showed an empty bundle (only future trades were tagged).
- A dead "blotter vs BNP" panel was still rendered on the Ladder.
- Stale documentation (README, upload guide, live-feed guide, launcher hints) that
  still described the BNP upload.
