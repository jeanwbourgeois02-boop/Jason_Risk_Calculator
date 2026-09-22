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
   - WARNING "no strike on file": open the Blotter's **Manual entry** sub-tab (the same
     "Option terms" editor is also under Options), pick the option and type its terms
     once; they survive every re-upload. For the reference export the terms come from
     the old Excel's "All Options Trades" sheet (checked 2026-09-18):
       - USDJPY111926P-197571137: Digital, Put, strike 152
       - USDJPY111926P-197957397: Digital, Put, strike 152
       - EURSEK112526C-197906813: Digital, Call, strike 11.4
     The diagnostics panel shows the current list.
   - FAIL or "not returned" on an NDF fixing ticker (`BZFXPTAX Index`, `KFTC18 Index`,
     `INRFBIL Index`, `TAIFX1 Index`, `JISDOR Index`; the "official fixing" rows of the
     Bloomberg library): these spellings are UNVERIFIED (2026-09-22). Look the fixing up
     on the Terminal (PTAX for BRL, KFTC18 for KRW, FBIL for INR, TAIFX1 for TWD, JISDOR
     for IDR) and correct the ticker in `data/ingest/common.py::NDF_FIX_TICKERS`; until
     then a fixed NDF's P&L says "no official fixing on file" and uses the fixing day's
     spot instead.
   - A trade the export does not carry at all (an option booked at another venue, a
     forward dealt outside the prime broker): book it under Blotter > Manual entry >
     "Book an OTC trade by hand". It is saved as source MANUAL, priced on the next pull,
     and never removed by a blotter upload; delete it there if it is wrong.
   - "FX vol ticker assumptions": the option Greeks rest on Bloomberg vol tickers that
     were written without a Terminal. Every live pull with an option in the book checks
     them against Bloomberg's own answers and records the result, so this line reads
     WARNING "not yet exercised" until the first such pull, then PASS, or FAIL naming
     the exact ticker or field Bloomberg rejected. Nothing to run by hand; a FAIL is a
     ticker-naming fix in `data/bloomberg/vol_marketdata.py`.

   - FAIL "Last marks pull ... looks stale": the last pull asked for zero marks but the
     book now needs some. Press Pull now.
5. If anything is still FAIL after a Pull now, wait for the next automatic pull
   (every 2 minutes) and re-check. Some data, such as a freshly typed option strike,
   only prices on the next full cycle.
