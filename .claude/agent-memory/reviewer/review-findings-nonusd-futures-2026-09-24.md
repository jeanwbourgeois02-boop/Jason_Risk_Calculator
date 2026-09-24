---
name: review-findings-nonusd-futures-2026-09-24
description: 2026-09-24 review of non-USD futures conversion (valuation/ledger/futures_delta/positions): no criticals; m_day-vs-expiry spot jump, equity-index line sums commodities, settled cash labels CNY P&L as USD
metadata:
  type: project
---

Commodity fork (Jason Risk Monitor, forked 2026-09-23). Change: FUTURE / EQ_OPTION P&L in quote ccy x S (usd_per_quote at as_of; frozen at S of m_day = FUTURE_PX's date). Three lanes (pnl-valuation, pnl-ledger, book-positions), uncommitted working tree.

Verified with an in-memory probe (schema.connect(), CNY future, USDCNY SPOT): open row on expiry day, provisional `_frozen_row` and ledger row agree (to ~1e-11, operation order) when FUTURE_PX exists on the expiry date; INTERP conversion spot freezes labelled and re-freezes when the real close lands; USD contracts bit-for-bit (s = 1.0 multiplies exactly). Golden book passes.

Open findings (relayed, not fixed):
- W: when the last FUTURE_PX is before expiry (last trade day before the leg's settle_date, or expiry-day close missing), open row converts at S(as_of) but the freeze uses S(m_day): LTD jumps the day after expiry (probe: 675.68 -> 714.29 USD). Contract text says exactly this, so it needs the user's call.
- W: engine/ladder/exposure_adapter.py settled cash puts a future's realised pnl_usd under currency USD; for a CNY future the variation margin is CNY (ladder-grid lane).
- W: `_provisional` reason says "no official mark on or before its settlement" when only the conversion SPOT is missing.
- W: no test pins open(expiry day) == provisional == ledger for a non-USD future.
- N: positions.equity_index_positions sums every FUTURE (copper tonnes + ES points) into the "Equity index" line; Phase 3 replaces it.
- N: `_settled_future_row` re-looks-up S for display; can disagree with the stored freeze until the ledger re-runs.

**How to apply:** on the next pass over futures P&L check these first; the Phase 2/3 lanes (curve-positions, ladder-grid) own the settled-cash and exposure pieces. Consumer tests in this repo take minutes; test_risk errors with ImportError in this environment (baseline, check before blaming a diff).
