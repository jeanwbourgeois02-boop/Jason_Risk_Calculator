# What P&L is available on day one

Written 2026-09-18, after the historical backfill gained forward outrights and futures
settles. "Day one" means a PC that connects to Bloomberg for the first time with a
book already loaded.

## Available from the first launch

- **FX forwards and spot**: the backfill writes, for every business day from the
  earliest trade date to yesterday, that day's SPOT close per pair (crosses such as
  EURSEK included, plus the USD pairs needed to convert them) and a FWD_OUTRIGHT for
  every open leg at its own settle date (a leg settling that day is marked at that
  day's spot; broken dates are interpolated between standard tenors and written as
  BBG_INTERP, which is official wherever Bloomberg has no direct outright).
- **Futures**: the daily settle for every open future, including the expiry day itself,
  so a future can be frozen even on a PC first connected after it expired.
- Therefore LTD, Daily, 5d, MTD and YTD are priceable for the FX and futures book on
  the first launch, once the backfill has run (it starts in the background after the
  first live pull and reports progress in the Market data tab).

## Not available on day one

- **Swaps** (IRS): OIS curve quotes and fixings are only pulled for today. A swap has
  no PV for any date before the first live pull, so its Daily/5d/MTD/YTD begin the day
  after the first pull. The header and Blotter strips show the swap as excluded on
  those reference dates rather than blanking the book.
- **FX options**: vol quotes are only pulled for today, same consequence.

Both are correctly missing, not wrong: nothing invents a past curve or vol.

## What a follow-up would need

`RatesBloombergSource.get_curve_quotes(ccy, as_of)` and
`VolBloombergSource.get_vol_quotes(pairs, as_of=...)` already request historical data
for a past date, and the curve and vol tables are keyed by date, so a historical
rates-and-vol backfill is mostly a loop over past business days calling those and then
pricing as of each day. Two open points: how far back Bloomberg serves these tickers,
and the request volume for vols (pairs × tenors × quote types), which needs the same
batching the forward backfill uses.
