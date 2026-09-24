---
name: phase5-options-lme-2026-09-24
description: Commodity Phase 5 (2026-09-24) in live.py - option contract dates (own fields, own request), CMDTY_OPTION at PX_MID, options step for CMDTY-only books, _lme_step and status["lme"] shape, the reviewer's LME source/key constraint, test traps
metadata:
  type: project
---

Housekeeper brief 2026-09-24 (Phase 5: options on commodity futures, LME forwards).

**Contract dates:** `library.contract_dates_needed` entries carry `product` and `fields`. The step
groups tickers by field tuple, one ReferenceDataRequest each (futures' FUT_LAST_TRADE_DT /
FUT_NOTICE_FIRST, options' OPT_EXPIRE_DT / LAST_TRADEABLE_DT). An option stores the first
field that parses as its last trade date, first notice ''. `_bloomberg_said_for` now walks back
through the trailing run of same-purpose requests, since there can be two. Summary splits
"N futures and K options on futures moved" (option told by data.contracts.tickers.parse_option_ticker).

**PX_MID:** `_listed_option_ids(conn)` = instruments whose asset_class, or whose trades'
product, is in engine.pnl.valuation.LISTED_OPTION_PRODUCTS (EQ_OPTION, CMDTY_OPTION).
Underlying futures (role UNDERLYING) are plain FUTURE_PX library rows: build_requests
already asks them, deduped by key with the traded future.

**Options step:** counts FX_OPTION + CMDTY_OPTION; skipped string "no option trades to price";
adds `futures_options_priced` and `futures_options_summary` ("N options on futures priced").
recalc_summary appends "; N options on futures priced" and says "no option on file".

**LME:** build_requests drops every `library.is_lme_row` row. The cash SPOT would otherwise be
asked in the FX spot step, and 'LME:CA' is 6 chars, so `_ensure_fx_instruments` would take it
for a pair. `_lme_step(conn, today, get_session, snapped)` runs after the futures marks
and before curves/vol/options/ledger, and in the no-requests branch too (an LME-only book).
Its time goes under timings["forwards"] (TIMING_KEYS unchanged). status["lme"] = {roots,
written, interp_written, missing [{root_id, ticker ('' for a prompt), settle_date, reason}],
reasons (helper's strings), summary}. Missing items also go into warnings.
The reviewer's constraint: write `fwd_curve.lme_curve_marks` rows as they come. The P&L reads
cash only as SPOT/BBG_BFXFORWARD with settle_date = as_of_date. Never re-source (BBG_BDH) or
re-key onto the cash date. A test pins it.

**Test traps:** bash heredoc with mixed quotes failed again: write test blocks to the
scratchpad with Write, then `cat >> file`. Never `py -3 -` with a heredoc: it opens the REPL
and floods output. `test_availability_no_blpapi_never_touches_the_socket` fails on this PC
(blpapi installed): environmental.
