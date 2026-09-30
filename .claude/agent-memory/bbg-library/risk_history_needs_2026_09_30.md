---
name: risk-history-needs-2026-09-30
description: library.risk_history_needs (Risk tab's Bloomberg history into price_history) is computed at read time, never stored in bbg_library; choices made on its scope
metadata:
  type: project
---

User decision 2026-09-30: the Risk tab stops reading the research app's rv.sqlite; its history comes from Bloomberg on "Pull Bloomberg now" into `price_history` (ingest-schema's table), asked by bbg-backfill from `library.risk_history_needs(conn, as_of)`.

Choices made (brief left them to this lane):
- Read time, not stored in `bbg_library`, no LIBRARY_VERSION bump (compute unchanged). **Why:** a root's whole chain is not a per-trade need, and contract dates move when Bloomberg's are stored (no trigger), same reason CONTRACT_DATES is met at read time ([[commodity-futures-library-2026-09-24]]).
- Held = net non-zero per instrument on as_of (LME: per root and prompt); a flat instrument adds nothing. FX currencies are not netted.
- CNY and CNH map to USDCNH (research app's rule, the hedge pair), not `_usd_pair_name`'s USDCNY that CONVERSION rows use.
- Chain = root's `active_months` from a month before the window start to the first cycle month after the furthest held, plus held months; skip any whose last trade date (stored, else estimate) is before the window start. start = window start (contract-master has no listing date).
- Window 900 calendar days (raised from 760 the same day: 500 observations on a ~245-day Chinese calendar). Sample book: 433 securities, 0.06 s.

**How to apply:** a change to what risk needs (deeper window, more LME pillars, per-contract volume) is edited here, not in `compute`; not shown in `tickers()` / `summary()` so the header's "N tickers needed" stays the P&L library.
