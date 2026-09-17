# Bloomberg PC checklist

Follow this on the PC that has the Bloomberg Terminal, every time you start the app.

1. Launch with `pnl`. A browser tab opens on the risk monitor.
2. Upload the trade blotter (top of the page, "Upload trade file") and press Confirm.
   Uploading replaces every trade already in the database, so there are never
   duplicates from an older file or from sample data.
3. Go to the Market data tab and press **Pull now**. Do this once right after every
   upload. The automatic feed re-pulls every 2 minutes, and its very first pull at
   start-up runs before any blotter is loaded, so it always asks Bloomberg for nothing.
4. Press **Check Bloomberg connection** (bottom of the Market data tab) and read every
   line. What each result means:
   - PASS on "blpapi installed" and "Terminal answers": nothing to do.
   - FAIL on SPOT / FWD_OUTRIGHT / FUTURE_PX coverage: press Pull now again. If it
     stays FAIL, the Terminal may not be logged in, or the ticker is wrong for that
     instrument. The pull table on the Market data tab shows the detail per instrument.
   - FAIL on curve quotes (for example "No SOFR curve quotes for <date>"): a live
     interest-rate swap needs an OIS curve. Press Pull now. If it recurs, the Terminal
     may lack curve permissions.
   - FAIL on FX option PREMIUM / DELTA coverage: the option needs its strike on file
     before it can price. See the next item.
   - WARNING "no strike on file": open the Blotter's Options view and type the strike
     for that trade. On 2026-09-17 these were EURSEK112526C-197906813,
     USDJPY111926P-197571137 and USDJPY111926P-197957397. The diagnostics panel shows
     the current list.
   - WARNING "FX vol ticker assumptions": the option Greeks rest on Bloomberg tickers
     that have not been verified on a real Terminal. Run this in a terminal on the
     Bloomberg PC and tick off each item it reports:

     ```
     py -3 -m data.bloomberg.vol_marketdata --probe
     ```

   - FAIL "Last marks pull ... looks stale": the last pull asked for zero marks but the
     book now needs some. Press Pull now.
5. If anything is still FAIL after a Pull now, wait for the next automatic pull
   (every 2 minutes) and re-check. Some data, such as a freshly typed option strike,
   only prices on the next full cycle.
