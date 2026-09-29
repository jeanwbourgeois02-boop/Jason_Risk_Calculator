---
name: fault-isolation-2026-09-29
description: Phase G "Smooth and contained" in bbg-curves - per-ticker isolation of every requester, the one NOT_A_NUMBER reason, writers that skip non-finite values, what was deliberately not added (split retry)
metadata:
  type: project
---

User, 2026-09-29 (Phase G): a bad pull must never crash everything; above all everything must pull
correctly. bbg-live made the live cycle fault-isolated first; bbg-curves matched it in its files.

**Rules now in the three modules:**
- Every requester reads each security inside its own try (`request_fwd_curves` -> `_fwd_curve_answer`,
  `request_lme_pillars` -> `_lme_pillar_answer`, `RatesBloombergSource._fetch_reference` /
  `_fetch_historical_single` with an optional `failures` dict, `vol_marketdata._read_one`). An
  unreadable answer is "answer not readable: <Type>: <msg>" on that ticker only.
- One reason string for a value that is not a finite number ('N.A.', NaN, inf, an empty bulk row):
  `NOT_A_NUMBER = "Bloomberg sent a value that is not a number"`, defined in fwd_curve, rates_marketdata
  and vol_marketdata (copied, not imported: those modules keep no import dependency on each other).
- Writers (`write_curve_quotes`, `write_vol_quotes`) skip non-finite values with an optional `rejected`
  list; a NaN used to hit the NOT NULL column (sqlite binds NaN as NULL) and lose the whole write.
- Additive shapes: FWD_CURVE answer gains `skipped` (row reasons); `historical_curve` gains `skipped`;
  `CurveSnapshot.failed` {ticker: reason} (compare=False, not in to_dict).
- Vol non-number is diagnostics bbg_status "NO_VALUE" (not a new status): tools/bbg_diagnostics treats
  any outcome but "OK" as failed, so a new status would be an interface change for nothing.

**Not added, on purpose:** a ticker-by-ticker split retry inside rates / vol. After per-ticker parse
isolation the only raises left are request-level (session, responseError, 3 timeouts), which a split
would repeat N times. The live cycle's chunking / per-metal retry is bbg-live's (live.py).

**How to apply:** a new requester in these files reads each security in its own try and uses
NOT_A_NUMBER; it takes no connection, so it can never hold the write lock while it waits.
Tests owed (user: no tests until the site is final) are listed in the 2026-09-29 report.
