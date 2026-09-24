---
name: phase5-cmdty-options-lme-2026-09-24
description: Phase 5 in backfill.py -- CMDTY_OPTION asked under option_request_ticker, untraded underlying asked via library UNDERLYING rows, price_close on CMDTY-only days, LME curve step (daily PX_LAST 17:00, helper rows written as they come), is_close_row(instrument_id=) LME rule and who must pass it.
metadata:
  type: project
---

Housekeeper brief 2026-09-24 (Phase 5 of the commodity conversion). Follows
[[commodity-futures-request-ticker-2026-09-24]] and [[phase2-macro-removal-2026-09-24]].

- **LME past close = daily PX_LAST stamped 17:00 NY** (`settle_stamp`), like futures, never the
  FX 15:00 bar (housekeeper took the recommended option under the user's listed-instrument rule).
  `is_close_row(..., instrument_id=)` applies it when the id is a contract root ('LME:CA');
  without the id a SPOT/FWD row is read as FX. **Why it matters:** inventory's close_completeness
  and lme_curve_status call is_close_row WITHOUT the id (bbg-library's code) -- until they pass
  it, a past LME day never counts complete and is re-asked. Requested of bbg-library 2026-09-24.
- **Reviewer constraint (2026-09-24):** the P&L reads LME cash only as SPOT / BBG_BFXFORWARD keyed
  settle_date = as_of_date, pillars/prompts as FWD_OUTRIGHT BBG_BFXFORWARD or BBG_INTERP. So
  `fwd_curve.lme_history_marks` rows are written AS THEY COME, never re-sourced (BBG_BDH, the
  futures habit, would blank every LME ticket). Tell LME rows by instrument, never by source.
- LME step: `_lme_rows_in_range` (needed_in_range include_lme=True + is_lme_row), `_lme_plan`
  per day (through = furthest open prompt, pillars via library.lme_curve_pillars),
  `_fetch_lme_history` with the futures' fetcher (fut_fetch) one call per stretch, rows built in
  PASS 1 (an LME ticket freezes at the last cash SPOT on or before its prompt). Pass 2 skips LME
  FWD_OUTRIGHT items (inventory._needed_marks does list them; the FX curve must not see them).
  Helper signature as landed: (root_id, day: date, pillars, closes {ticker: float},
  open_prompts [date], snapped_at) -> (rows, reasons: [str]); its reasons include informational
  ones (computed prompt dates), so they are only surfaced on a needed mark that was not made.
- CMDTY_OPTION: instrument asset_class 'CMDTY_OPTION' (ingest), root in base_ccy;
  `option_for(root, id, conn=conn)` uses the booked expiry. The underlying future has an
  instruments row with no trade (ingest writes it) and a library FUTURE_PX row role UNDERLYING,
  so the ordinary futures path asks it. `_options_open_on` counts a CMDTY_OPTION open until its
  NOTIONAL leg's settle date. Day key / status options block: `futures_options_priced`.
- Day results gained `lme_marks` and `futures_options_priced` (in `_STEP_KEYS`).
- Trap: never `py -3 -` in Bash (it hung waiting on stdin and had to be killed); use Edit for
  test edits and heredoc-free commands. A bash heredoc with nested quotes also failed to parse
  in this harness (nothing appended) -- use the Edit tool for appends.
