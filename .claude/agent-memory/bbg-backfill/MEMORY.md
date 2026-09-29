# bbg-backfill agent memory

Older notes on backfill.py live in `.claude/agent-memory/bbg-data/` (lane split 2026-09-24).

- [Commodity futures request ticker (2026-09-24)](commodity_futures_request_ticker_2026_09_24.md) — one-digit live / two-digit expired form chosen on the request day; placeholders never asked; library `requestable`
- [Phase 5 CMDTY options + LME (2026-09-24)](phase5_cmdty_options_lme_2026_09_24.md) — LME close 17:00 PX_LAST, is_close_row(instrument_id=), helper rows never re-sourced, option request tickers
- [Phase 2 macro removal (2026-09-24)](phase2_macro_removal_2026_09_24.md) — NDF_FIX, swap re-pricing, rates_* keys gone; status "rates" -> "inputs"; options read curve_quotes, no bootstrap needed
- [Futures 15:00 close built and reversed (2026-09-28)](futures_1500_close_reversed_2026_09_28.md) — FUTURE_PX stays daily PX_LAST 17:00; the abandoned 15:00 TRADE-bar design, in case it returns
- [One close, 17:00 NY for every instrument (2026-09-28)](one_close_1700_every_instrument_2026_09_28.md) — 15:00 intraday FX rule gone; close_stamp(day), settle_stamp alias, is_close_row ignores today/instrument_id, CLOSE_HOUR_NY local
- [Fault isolation, Phase G (2026-09-29)](fault_isolation_2026_09_29.md) — _Asks chunks/split/timeout give-up/lock release, not-a-number refusals, guarded steps, transient retry, progress; tests it flips
