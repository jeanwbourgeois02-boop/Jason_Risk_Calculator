---
name: close-1500-scale-ndf-2026-09-21
description: 2026-09-21 user decisions in data/bloomberg - FWD_POINTS_SCALE returned nothing on the terminal (FWD_SCALE fallback, 20 % guard and its limits), official close moved to 15:00 NY via IntradayBarRequest (UNVERIFIED), close-stamp rule for past FX rows, NDF_1M library kind; traps found on the way.
metadata:
  type: project
---

Follows [[historical-backfill-no-settle-dt-2026-09-21]]. Three user decisions, all 2026-09-21.

## A. Points divisor: FWD_POINTS_SCALE did not answer on the Bloomberg PC
- Paste: "Bloomberg returned forward points for AUDUSD but no FWD_POINTS_SCALE" (45 of 67 marks of the
  2026-09-14 close). So the tenor tickers `<PAIR><TENOR> Curncy` DO answer with points in history; the
  field name was the guess that failed.
- `pull_marks.fetch_points_scales` (shared by backfill and the live tenor path): ONE ReferenceDataRequest
  for both `FWD_POINTS_SCALE` (divisor as is) and `FWD_SCALE` (decimal places, divisor = 10 ** n, whole
  number 0..8). `fetch_reference` already tolerates a field exception per field (the other field is still
  returned); Bloomberg's message is read back from a throwaway `Diagnostics` so a reason can repeat it.
- **FWD_SCALE semantics are still UNVERIFIED.** Expect 4 for EURUSD and 2 for USDJPY; the probe step
  `fwd_points_scale` (pull_marks --probe) and the `fwdscale` check (tools/bloomberg_terminal_probe.py,
  what `pnl doctor --bloomberg` runs) print both fields for both pairs.
- The 20 %-of-spot guard (`points_outright_is_plausible`, per written forward) is WEAK by construction:
  it cannot see a divisor that is too large, nor a 10x error, nor a 100x error on a low-carry pair at a
  short tenor (EURUSD 1M at 100x is only +17 %). It also refuses a legitimate high-carry leg beyond about
  six months (USDTRY). A real check would compare against Bloomberg's own FWD_CURVE outrights from the
  live pull (already on file as official tenor rows); not built, offered to the user.

## C. Official close = 15:00 New York (was 17:00)
SUPERSEDED 2026-09-22 for the cut-over part: the 15:00 close now applies to every past day within
intraday reach and `CLOSE_1500_FROM` is gone, see [[close-1500-every-past-day-2026-09-22]]. The
IntradayBarRequest shape, the traps and the fixture note below still hold.
- One constant: `pull_marks.CLOSE_HOUR_NY`. `backfill.close_stamp` and `pull_marks.snapped_at` read it.
  NOT covered (other lanes): `engine/rates/store.py::snapped_at` still stamps 17:00, and vol_quotes /
  rate_vol_quotes and every pricer mark take their stamp from it.
- Past FX closes (SPOT + tenor series) come from `pull_marks.fetch_intraday_close_series`: two
  IntradayBarRequests per ticker per stretch (BID, ASK), interval 60, naive-UTC start/end,
  gapFillInitialBar; per day the bar STARTING 14:00 NY (18:00 UTC in EDT, 19:00 UTC in EST, resolved per
  date), mid of the two closes. Same return shape as fetch_historical_series under the key "PX_LAST";
  a day with no close carries `{CLOSE_REASON: why}` instead. **Entirely UNVERIFIED on a terminal**
  (request shape, BID/ASK event types on Curncy and on points tickers, bar time = bar start).
  gapFillInitialBar only fills the FIRST bar of a response, so a quiet hour mid-stretch is simply absent.
- Intraday history is about 140 business days (`INTRADAY_HISTORY_BUSINESS_DAYS`). An older day is not
  asked for at all and stays missing (never PX_LAST, hard rule 2). Consequence: the YTD reference date
  (last business day of the previous year) is out of reach from roughly mid-July each year.
- Close-stamp rule (`backfill.is_close_row`): a PAST day's official SPOT / FWD_OUTRIGHT whose snapped_at
  is not the close stamp of its own date is not a close. `inventory.close_completeness` counts it as
  missing (new column `not_closed`, new param `today`), and `backfill._write_closes` replaces it. Today's
  rows and FUTURE_PX are exempt; realised_pnl is never touched.
- **Trap:** the replacement must DELETE the stale row of the other source in the same transaction. A
  stale BBG_BFXFORWARD live tenor row beats a new BBG_INTERP close in `marks_official` (direct quote
  wins), so without the delete the day stays "not closed" forever.
- **Trap:** changing what close_completeness calls missing breaks fixtures outside this lane that stamp
  past marks `T17:00:00-04:00` (tests/test_header.py `_insert_official_mark`, tests/test_ui_market_data.py
  `_mark`). The header's P&L itself does not look at stamps, only its "missing" sentence does.
- Cost: a stretch now costs 2 x (spot tickers + tenor tickers) requests instead of 2. The first run after
  the change re-requests every past day once (status "backfill" -> "note").

## B. NDF 1M
- Library kind `NDF_1M` (in LIVE_ONLY_KINDS, not MARK_KINDS, so never a past-close need and never in
  `_needed_marks`): key = the NDF currency's USD pair also for a cross, ticker from
  `data.ingest.common.NDF_1M_TICKERS`, until the trade's last leg / option expiry.
- Live pull: asked in the SAME ReferenceDataRequest as the spots (`live.SPOT_LIKE_MARK_TYPES`), written
  on the USD pair dated today. **Trap:** NDF_1M and SPOT share instrument_id, so `spot_by_pair` must be
  built from SPOT rows only or the 1M price marks legs that settle today.

## Working-tree traps
- `grep -c $'\r'` in this Git Bash counts every line; check endings with Python bytes instead.
- tests/test_backfill.py has MIXED line endings at HEAD. Rewriting a whole file normalises them and shows
  ~100 phantom changed lines; restore untouched lines from `git show HEAD:` bytes (difflib on stripped lines).
- The Edit tool strips trailing whitespace from new_string (it turned `"Finished: {` into `"Finished:{`).
